import { useState } from 'react'

import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    receiveItems: vi.fn(),
    listStorageLocations: vi.fn(),
    uploadImage: vi.fn(),
    getInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
    updateInventoryItem: vi.fn(),
  },
}))

import { api } from '../../api'
import ReceiptPanel from './ReceiptPanel'
import { renderWithProviders } from '../../../test/helpers'

const LOCATIONS = [{ id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' }]

const ITEM = {
  id: 412,
  item_code: 'CC-000412',
  description: '1881-S Morgan $1',
  reviewed: [],
}

// Reproduces the shape of the real parent (`Receiving.jsx`): `itemIds` is
// owned by something above `ReceiptPanel` and can change out from under it
// while review is open, the same as ticking or unticking a checkbox in
// `OutstandingList` would.
function SelectionOwner({ initialIds }) {
  const [ids, setIds] = useState(initialIds)
  return (
    <>
      <button onClick={() => setIds((prev) => prev.slice(0, -1))}>Untick last</button>
      <button onClick={() => setIds((prev) => [...prev, 413])}>Tick second</button>
      <ReceiptPanel itemIds={ids} onDone={vi.fn()} />
    </>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listStorageLocations.mockResolvedValue(LOCATIONS)
  api.receiveItems.mockResolvedValue({ received: 2 })
  api.uploadImage.mockResolvedValue({ id: 1 })
  api.getInventoryItem.mockResolvedValue(ITEM)
  api.setItemReview.mockResolvedValue({ reviewed: [] })
  api.updateInventoryItem.mockResolvedValue({})
})

describe('ReceiptPanel', () => {
  it('sends one request for several items, not one each', async () => {
    // A box of twenty coins is one transaction. Twenty requests would leave a
    // partial state nobody can describe if the tenth failed.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(1))
    expect(api.receiveItems.mock.calls[0][0].item_ids).toEqual([412, 413])
  })

  it('sends the outcome the pressed button names', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].outcome).toBe('missing')
  })

  it('defaults the arrival date to the local calendar date, not UTC, but lets it be changed', async () => {
    // Pinned rather than read from the real clock: at whatever instant this
    // suite happens to run, local and UTC dates might agree, and a test that
    // only fails during some hours of the day is worse than no test. This
    // machine's zone is America/New_York (UTC-4 in September); the instant
    // below is 2026-09-09 22:00 local but already 2026-09-10 in UTC, so a
    // component that reverted to `toISOString()` would show the wrong day.
    // Faking only `Date` (not timers) -- faking setTimeout/setInterval as
    // well stalls React's own scheduling and testing-library's async
    // queries, which is why an earlier draft of this test hung.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-10T02:00:00.000Z'))
    try {
      renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
      const field = await screen.findByLabelText(/arrived/i)
      expect(field).toHaveValue('2026-09-09')
      // Guards against exactly the regression this test exists to catch --
      // without it, a revert to the UTC-based default would still pass the
      // assertion above only by coincidence outside the pinned instant.
      expect(field).not.toHaveValue('2026-09-10')
    } finally {
      vi.useRealTimers()
    }

    const field = screen.getByLabelText(/arrived/i)
    await userEvent.clear(field)
    await userEvent.type(field, '2026-09-04')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].arrived_on).toBe('2026-09-04')
  })

  it('reports a refused receipt without clearing what was typed', async () => {
    // A 409 means the wrong row, or a double submit. Making the operator
    // retype the note they just wrote turns a recoverable mistake into a
    // reason to skip the note next time.
    api.receiveItems.mockRejectedValue(
      Object.assign(new Error('Already received: [CC-000412]'), { status: 409 }),
    )
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    const note = await screen.findByLabelText(/note/i)
    await userEvent.type(note, 'edge knock')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    expect(await screen.findByText(/already received/i)).toBeInTheDocument()
    expect(note).toHaveValue('edge knock')
  })

  it('does nothing at all when no item is selected', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[]} onDone={vi.fn()} />)
    expect(await screen.findByRole('button', { name: /^receive$/i })).toBeDisabled()
  })

  it('keeps the confirm-and-correct section out of the way until asked', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    expect(screen.queryByLabelText(/grade/i)).not.toBeInTheDocument()

    await userEvent.click(
      await screen.findByRole('button', { name: /confirm or correct/i }),
    )
    expect(await screen.findByLabelText(/grade/i)).toBeInTheDocument()
  })

  it('uploads a photograph against the one item it was taken of', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    const input = await screen.findByLabelText(/photo/i)
    expect(input).toBeEnabled()

    await userEvent.upload(
      input,
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.uploadImage).toHaveBeenCalled())
    expect(api.uploadImage).toHaveBeenCalledWith(
      412,
      expect.any(File),
      expect.objectContaining({ isPrimary: true }),
    )
  })

  it('will not guess which item a photograph belongs to when several are selected', async () => {
    // A photograph is evidence of one physical object. Attaching it to
    // every selected item (or to "the first" of them) would put a wrong
    // provenance record on the rest, and a wrong record reads as a right
    // one -- worse than no photograph at all. This is the regression the
    // `flatMap` implementation had: it would fail this test.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)

    const input = await screen.findByLabelText(/photo/i)
    expect(input).toBeDisabled()
    expect(screen.getByText(/photographs attach to a single item/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.uploadImage).not.toHaveBeenCalled()
  })

  it('a failed photograph does not undo the receipt', async () => {
    // The arrival is the fact; the photograph is evidence added to it. Losing
    // a recorded arrival because an upload failed is the worse trade, so the
    // upload is reported and retryable, not rolled back.
    api.receiveItems.mockResolvedValue({ received: 1 })
    api.uploadImage.mockRejectedValue(new Error('upload failed'))

    const onDone = vi.fn()
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={onDone} />)
    await userEvent.upload(
      await screen.findByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument()
    expect(onDone).toHaveBeenCalled()
  })

  it('keeps the review queue frozen when the selection changes underneath it', async () => {
    // Reproduces the reviewer's probe: two ids, open review, press Next,
    // untick the second item. Without a frozen snapshot, ReviewPane's `ids`
    // prop shrinks to one entry while its internal `at` index is still 1,
    // so `ids[at]` is `undefined` and it renders "2 of 1".
    renderWithProviders(<SelectionOwner initialIds={[412, 413]} />)

    await userEvent.click(
      await screen.findByRole('button', { name: /confirm or correct/i }),
    )
    expect(await screen.findByText('1 of 2')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^next$/i }))
    expect(await screen.findByText('2 of 2')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /untick last/i }))

    expect(screen.getByText('2 of 2')).toBeInTheDocument()
    expect(screen.queryByText(/2 of 1/)).not.toBeInTheDocument()
    expect(api.getInventoryItem).not.toHaveBeenCalledWith(undefined)
  })

  it('clears a stale upload error once a later receipt carries no photograph', async () => {
    // A failed photo upload's message must not survive a later receipt that
    // has no photograph of its own to fail -- that would report a failure
    // that did not happen.
    api.uploadImage.mockRejectedValue(new Error('upload failed'))
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    await userEvent.upload(
      await screen.findByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/upload failed/i)).not.toBeInTheDocument()
  })

  it('names a pending photo that a second selected item would strand', async () => {
    // The submit-time guard already refuses to attach the photo when more
    // than one item is selected -- that is correct, but silent. The hint
    // must name the file so the operator knows it was dropped, not lost.
    renderWithProviders(<SelectionOwner initialIds={[412]} />)

    await userEvent.upload(
      await screen.findByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )

    await userEvent.click(screen.getByRole('button', { name: /tick second/i }))

    expect(
      await screen.findByText(/obverse\.jpg.*will not be uploaded/i),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.uploadImage).not.toHaveBeenCalled()
  })
})
