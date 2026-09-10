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
    // Reachable once a currency item is selected -- FriedbergLookup calls
    // these itself, but it only mounts after "Look up Friedberg number" is
    // pressed, so most tests here never touch them.
    getSignatureCombinations: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
    attachFriedberg: vi.fn(),
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
  api.getSignatureCombinations.mockResolvedValue({ table: 'signature_combination', values: [] })
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
    // The suite runs in UTC (see vite.config.js) so that it matches the
    // database and stays reproducible everywhere -- but that is exactly the
    // one zone in which "local date" and "UTC date" always agree, so a UTC
    // runner can never catch a regression back to `toISOString()`'s date.
    // This test is the deliberate exception: it overrides `process.env.TZ` to
    // a real zone BEHIND UTC for its own duration and restores it afterwards,
    // rather than pinning the whole suite away from UTC for one test's sake.
    // Node re-reads `process.env.TZ` live -- confirmed empirically, including
    // for a `Date` object constructed before the override -- so this is not
    // an assumption.
    //
    // The instant below is 2026-09-09 22:00 in America/New_York but already
    // 2026-09-10 in UTC, so a component that reverted to `toISOString()`
    // would show the wrong day. Faking only `Date` (not timers) -- faking
    // setTimeout/setInterval as well stalls React's own scheduling and
    // testing-library's async queries, which is why an earlier draft of this
    // test hung.
    const previousTz = process.env.TZ
    process.env.TZ = 'America/New_York'
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
      process.env.TZ = previousTz
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

  it('does not offer a Friedberg lookup for a coin', async () => {
    // A coin has no Friedberg number; offering the lookup for one is an
    // invitation to the 404 the attach endpoint returns for an item with no
    // currency_detail. On its own, this would still pass a component that
    // hides the section unconditionally -- it is paired with the next test,
    // which the same component would fail if it ignored item_kind, so
    // together they show the gate actually reads it rather than hiding (or
    // showing) the section no matter what the item is.
    api.getInventoryItem.mockResolvedValue({ ...ITEM, item_kind: 'coin' })
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    await waitFor(() => expect(api.getInventoryItem).toHaveBeenCalledWith(412))
    expect(
      screen.queryByRole('button', { name: /friedberg/i }),
    ).not.toBeInTheDocument()
  })

  it('offers a collapsed Friedberg lookup for a currency item', async () => {
    // Would fail a component that always hides the section (the previous
    // test's failure mode) or that shows the lookup form immediately instead
    // of collapsed -- both are asserted here.
    api.getInventoryItem.mockResolvedValue({ ...ITEM, item_kind: 'currency' })
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    const toggle = await screen.findByRole('button', { name: /look up friedberg/i })
    // Collapsed by default: nothing about the lookup form is on screen, and
    // nothing about it has run, until the toggle is pressed.
    expect(screen.queryByLabelText(/denomination/i)).not.toBeInTheDocument()
    expect(api.searchFriedberg).not.toHaveBeenCalled()

    await userEvent.click(toggle)
    expect(await screen.findByLabelText(/denomination/i)).toBeInTheDocument()
  })

  it('does not offer a Friedberg lookup with several items selected', async () => {
    // Attaching needs one item id to name -- the same ambiguity a photograph
    // runs into with several selected. Asserting `getInventoryItem` was never
    // called is what makes this more than a restatement of the "hides for a
    // coin" test: a component that fetched anyway and only hid the button
    // would still fail this.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)
    await screen.findByRole('button', { name: /^receive$/i })
    expect(api.getInventoryItem).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: /friedberg/i }),
    ).not.toBeInTheDocument()
  })
})
