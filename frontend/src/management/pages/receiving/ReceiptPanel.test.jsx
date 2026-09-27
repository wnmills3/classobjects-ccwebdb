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
    getItemSales: vi.fn(),
    setItemReview: vi.fn(),
    updateInventoryItem: vi.fn(),
    // The Identify section's look-up of what the facts decide.
    suggestNote: vi.fn(),
    suggestCoin: vi.fn(),
    // Reachable once a currency item is selected -- FriedbergLookup calls
    // these itself, but it only mounts after "Look up Friedberg number" is
    // pressed, so most tests here never touch them.
    getSignatureChoices: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
    attachFriedberg: vi.fn(),
    // ErrorsPanel's own calls, reachable whenever exactly one item is
    // selected.
    getItemErrors: vi.fn(),
    setItemErrors: vi.fn(),
    // OffersPanel's, through the ItemEditForm that ReviewPane mounts.
    listListings: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
    createOffers: vi.fn(),
    // PhotosPanel's, through the same ItemEditForm.
    listItemImages: vi.fn(),
    // HistoryPanel's, through the same ItemEditForm.
    getItemHistory: vi.fn(),
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
  // Every real item has one, and the errors panel is not offered until this
  // panel knows it: `kind` decides which half of the error vocabulary the
  // picker shows and which `applies_to` a type added there is marked with.
  item_kind: 'coin',
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
  api.getItemSales.mockResolvedValue([])
  api.getItemHistory.mockResolvedValue([])
  api.listListings.mockResolvedValue([])
  api.listSalesVenues.mockResolvedValue([])
  api.listStorageLocations.mockResolvedValue(LOCATIONS)
  api.receiveItems.mockResolvedValue({ received: 2 })
  api.uploadImage.mockResolvedValue({ id: 1 })
  api.getInventoryItem.mockResolvedValue(ITEM)
  api.setItemReview.mockResolvedValue({ reviewed: [] })
  api.updateInventoryItem.mockResolvedValue({})
  api.suggestNote.mockResolvedValue({})
  api.suggestCoin.mockResolvedValue({})
  api.getSignatureChoices.mockResolvedValue({
    table: 'signature_combination',
    values: [],
  })
  api.getItemErrors.mockResolvedValue({ inventory_item_id: 412, errors: [] })
  api.listItemImages.mockResolvedValue([])
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
    expect(screen.queryByLabelText(/^grade$/i)).not.toBeInTheDocument()

    await userEvent.click(
      await screen.findByRole('button', { name: /confirm or correct/i }),
    )
    expect(await screen.findByLabelText(/^grade$/i)).toBeInTheDocument()
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

  it('a failed photograph does not undo the receipt, and does not signal done', async () => {
    // The arrival is the fact; the photograph is evidence added to it. Losing
    // a recorded arrival because an upload failed is the worse trade, so the
    // upload is reported, not rolled back.
    //
    // `onDone` is withheld, and that is the whole point: the caller closes a
    // dialog on it, which unmounts this panel. Reporting the failure and then
    // being unmounted writes the error where nobody can read it -- the
    // operator is told the receipt landed and never learns the photograph did
    // not. This test asserts against a mounted panel, so it cannot see that
    // on its own; `Receiving.test.jsx` covers the dialog staying open.
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
    expect(onDone).not.toHaveBeenCalled()
  })

  it('signals done, with what it was recorded against, when everything lands', async () => {
    api.receiveItems.mockResolvedValue({ received: 1 })
    const onDone = vi.fn()
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={onDone} />)

    await userEvent.selectOptions(await screen.findByLabelText(/storage/i), '3')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    // The caller offers these again on the next line.
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith(
        expect.objectContaining({ storageLocationId: '3' }),
      ),
    )
  })

  it('starts from the values it is given, so a parcel is picked once', async () => {
    renderWithProviders(
      <ReceiptPanel
        itemIds={[412]}
        onDone={vi.fn()}
        initial={{ storageLocationId: '3', arrivedOn: '2026-09-01' }}
      />,
    )
    expect(await screen.findByLabelText(/storage/i)).toHaveValue('3')
    expect(screen.getByLabelText(/arrived/i)).toHaveValue('2026-09-01')
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
    expect(screen.queryByRole('button', { name: /friedberg/i })).not.toBeInTheDocument()
  })

  it('offers a collapsed Friedberg lookup for a currency item', async () => {
    // Would fail a component that always hides the section (the previous
    // test's failure mode) or that shows the lookup form immediately instead
    // of collapsed -- both are asserted here.
    api.getInventoryItem.mockResolvedValue({ ...ITEM, item_kind: 'currency' })
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    const toggle = await screen.findByRole('button', { name: /look up friedberg/i })
    // Collapsed by default: nothing about the lookup form is on screen, and
    // nothing about it has run, until the toggle is pressed. (Web press is
    // the lookup's own: Identify asks a note's denomination too.)
    expect(screen.queryByLabelText(/web press/i)).not.toBeInTheDocument()
    expect(api.searchFriedberg).not.toHaveBeenCalled()

    await userEvent.click(toggle)
    expect(await screen.findByLabelText(/web press/i)).toBeInTheDocument()
  })

  it('offers the errors panel for the one selected item', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    await waitFor(() => expect(api.getItemErrors).toHaveBeenCalledWith(412))
    expect(await screen.findByRole('button', { name: 'Add error' })).toBeInTheDocument()
  })

  // The panel takes the item's kind, and an unknown kind is not the same as
  // "coin": `isCurrencyKind(null)` is false, so a banknote whose kind had not
  // arrived would be offered the sixteen COIN error types, and a type added
  // from that picker would be posted `applies_to: 'coin'` -- a mis-marked
  // vocabulary row that then vanishes from the very picker that created it.
  // On a `getInventoryItem` that fails, that is the steady state, not one
  // render, which is why both cases are pinned here.
  it('does not offer the errors panel until the item kind has arrived', async () => {
    // A fetch that never settles: the panel must not appear meanwhile.
    api.getInventoryItem.mockReturnValue(new Promise(() => {}))
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await screen.findByRole('button', { name: /^receive$/i })

    expect(screen.queryByRole('button', { name: 'Add error' })).not.toBeInTheDocument()
    expect(api.getItemErrors).not.toHaveBeenCalled()
  })

  it('does not offer the errors panel when the item could not be fetched', async () => {
    api.getInventoryItem.mockRejectedValue(new Error('gone'))
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await waitFor(() => expect(api.getInventoryItem).toHaveBeenCalledWith(412))
    await screen.findByRole('button', { name: /^receive$/i })

    expect(screen.queryByRole('button', { name: 'Add error' })).not.toBeInTheDocument()
    expect(api.getItemErrors).not.toHaveBeenCalled()
  })

  it('does not offer the errors panel, or call its API, with several items selected', async () => {
    // `PUT /api/inventory/{id}/errors` replaces one item's set -- there is no
    // bulk semantic to invent for several items received at once.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)
    await screen.findByRole('button', { name: /^receive$/i })

    expect(screen.queryByRole('button', { name: 'Add error' })).not.toBeInTheDocument()
    expect(api.getItemErrors).not.toHaveBeenCalled()
  })

  it("shows only the editor's own errors panel while the review pane is open, not a second one behind it", async () => {
    // ItemEditForm (mounted by ReviewPane) has its own ErrorsPanel for the
    // same item. Both are self-saving and each PUT replaces the item's whole
    // set, so if the per-line panel here stayed mounted too, an error added
    // in one would silently discard one added in the other.
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await screen.findByRole('button', { name: 'Add error' })
    expect(document.querySelectorAll('.errors-panel')).toHaveLength(1)
    expect(document.querySelector('.review-pane .errors-panel')).toBeNull()

    await userEvent.click(
      await screen.findByRole('button', { name: /confirm or correct/i }),
    )

    await waitFor(() =>
      expect(document.querySelectorAll('.errors-panel')).toHaveLength(1),
    )
    expect(document.querySelector('.review-pane .errors-panel')).not.toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /leave review/i }))

    // Back to the per-line panel once review closes.
    await waitFor(() =>
      expect(document.querySelectorAll('.errors-panel')).toHaveLength(1),
    )
    expect(document.querySelector('.review-pane')).toBeNull()
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
    expect(screen.queryByRole('button', { name: /friedberg/i })).not.toBeInTheDocument()
  })

  it("warns in the errors panel when the selected item's sale_state is not empty", async () => {
    // Task 8 wired `itemSaleState` into ErrorsPanel, but no test here ever
    // populated `sale_state` -- so until now the wiring was only checked by
    // reading the code. A component that dropped `sale_state` on the way to
    // ErrorsPanel, or never fetched it, would fail this.
    api.getInventoryItem.mockResolvedValue({
      ...ITEM,
      sale_state: [{ text: 'listing #3 at 189.00 on eBay' }],
    })
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    await waitFor(() => expect(api.getInventoryItem).toHaveBeenCalledWith(412))
    expect(await screen.findByRole('alert')).toHaveTextContent('This item is for sale')
    expect(
      screen.getByRole('checkbox', { name: /record it anyway/i }),
    ).toBeInTheDocument()
  })

  it('asks before marking a for-sale item missing, then resends acknowledged', async () => {
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems
      .mockRejectedValueOnce(
        Object.assign(new Error(detail), { status: 409, body: { detail } }),
      )
      .mockResolvedValueOnce({ received: 1 })

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('CC-000412')
    await userEvent.click(screen.getByRole('button', { name: 'Record it anyway' }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(2))
    expect(api.receiveItems.mock.calls[1][0]).toMatchObject({
      outcome: 'missing',
      acknowledge_for_sale: true,
    })
  })

  it('leaves the panel alone when the refusal is declined', async () => {
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems.mockRejectedValueOnce(
      Object.assign(new Error(detail), { status: 409, body: { detail } }),
    )

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Leave it on sale' }),
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.receiveItems).toHaveBeenCalledTimes(1)
  })

  it('closes the dialog and shows the failure when the acknowledged resend itself fails', async () => {
    // ModalDialog is a true modal (showModal()), so an error left behind a
    // dialog that is still open is an error the operator can never see --
    // this reproduces a resubmit that fails for a reason other than another
    // for-sale refusal (network, 500, an unrelated conflict).
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems
      .mockRejectedValueOnce(
        Object.assign(new Error(detail), { status: 409, body: { detail } }),
      )
      .mockRejectedValueOnce(
        Object.assign(new Error('Storage location is archived'), { status: 500 }),
      )

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))
    expect(await screen.findByRole('dialog')).toHaveTextContent('CC-000412')

    await userEvent.click(screen.getByRole('button', { name: 'Record it anyway' }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByText(/storage location is archived/i)).toBeInTheDocument()
  })

  it('carries the acknowledgement onto the photographs it uploads', async () => {
    // Ending the listing does not necessarily take the item off sale -- an
    // open order still holds it -- so `POST /api/images` asks the same
    // question the receipt just did. Without the flag the upload is refused
    // and the photograph is gone: the picker is cleared and no retry exists.
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems
      .mockRejectedValueOnce(
        Object.assign(new Error(detail), { status: 409, body: { detail } }),
      )
      .mockResolvedValueOnce({ received: 1 })

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.upload(
      await screen.findByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await userEvent.click(screen.getByRole('button', { name: /missing/i }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('CC-000412')
    await userEvent.click(screen.getByRole('button', { name: 'Record it anyway' }))

    await waitFor(() => expect(api.uploadImage).toHaveBeenCalled())
    expect(api.uploadImage).toHaveBeenCalledWith(
      412,
      expect.any(File),
      expect.objectContaining({ acknowledgeForSale: true }),
    )
  })
})

describe('ReceiptPanel: Identify', () => {
  const NOTE = {
    ...ITEM,
    item_kind: 'currency',
    denomination: 'usd_note_1',
    series_year: 1957,
    series_letter: 'B',
    serial_number: 'A1B',
    face_plate_number: null,
    back_plate_number: null,
  }

  async function openNote() {
    api.getInventoryItem.mockResolvedValue(NOTE)
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    return screen.findByRole('textbox', { name: /serial number/i })
  }

  async function retypeSerial(serial, text) {
    await userEvent.clear(serial)
    await userEvent.type(serial, text)
  }

  it("shows a note's identifying facts first, filled from the item", async () => {
    const serial = await openNote()
    expect(screen.getByRole('spinbutton', { name: /series year/i })).toHaveValue(1957)
    expect(serial).toHaveValue('A1B')
    // Above the parcel's own fields.
    const arrived = screen.getByLabelText(/arrived/i)
    expect(
      serial.compareDocumentPosition(arrived) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('saves only what changed, with what it was, before the receipt', async () => {
    const serial = await openNote()
    await retypeSerial(serial, 'A12345678B')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.updateInventoryItem).toHaveBeenCalledWith(412, {
      serial_number: 'A12345678B',
      base: { serial_number: 'A1B' },
    })
    expect(api.updateInventoryItem.mock.invocationCallOrder[0]).toBeLessThan(
      api.receiveItems.mock.invocationCallOrder[0],
    )
  })

  it('sends nothing to the item when nothing changed', async () => {
    await openNote()
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.updateInventoryItem).not.toHaveBeenCalled()
  })

  it('does not save Identify edits for Missing, and says so beforehand', async () => {
    const serial = await openNote()
    await retypeSerial(serial, 'A12345678B')
    expect(screen.getByText(/saved only with receive/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /missing/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.updateInventoryItem).not.toHaveBeenCalled()
  })

  it('a refused save receives nothing and keeps what was typed', async () => {
    api.updateInventoryItem.mockRejectedValueOnce(new Error('serial_number: too long'))
    const serial = await openNote()
    await retypeSerial(serial, 'A12345678B')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    expect(await screen.findByText('serial_number: too long')).toBeInTheDocument()
    expect(api.receiveItems).not.toHaveBeenCalled()
    expect(serial).toHaveValue('A12345678B')
  })

  it('a receipt that fails after the save does not send the save again', async () => {
    api.receiveItems
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ received: 1 })
    const serial = await openNote()
    await retypeSerial(serial, 'A12345678B')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
    expect(await screen.findByText('network down')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(2))
    expect(api.updateInventoryItem).toHaveBeenCalledTimes(1)
  })

  it('holds back the full editor while Identify has unsaved edits', async () => {
    const serial = await openNote()
    const confirm = screen.getByRole('button', { name: /confirm or correct fields/i })
    expect(confirm).toBeEnabled()

    await retypeSerial(serial, 'A12345678B')

    expect(confirm).toBeDisabled()
    expect(
      screen.getByText(/receive or undo the identify changes first/i),
    ).toBeInTheDocument()
  })

  it('sends the for-sale acknowledgement with the save as well', async () => {
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.updateInventoryItem
      .mockRejectedValueOnce(
        Object.assign(new Error(detail), { status: 409, body: { detail } }),
      )
      .mockResolvedValueOnce({})
    const serial = await openNote()
    await retypeSerial(serial, 'A12345678B')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('CC-000412')
    expect(api.receiveItems).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Record it anyway' }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(1))
    expect(api.updateInventoryItem.mock.calls[1][1]).toMatchObject({
      serial_number: 'A12345678B',
      acknowledge_for_sale: true,
    })
    expect(api.receiveItems.mock.calls[0][0]).toMatchObject({
      acknowledge_for_sale: true,
    })
  })

  it('says what the facts decide', async () => {
    api.suggestNote.mockResolvedValue({
      note_type: 'silver_certificate',
      seal_color: 'blue',
      signature_combination: null,
      fed_district: null,
      series: null,
      warning: null,
    })
    await openNote()

    await waitFor(() =>
      expect(api.suggestNote).toHaveBeenCalledWith(
        expect.objectContaining({
          denomination: 'usd_note_1',
          series_year: '1957',
          series_letter: 'B',
        }),
      ),
    )
    expect(await screen.findByText(/silver_certificate/)).toBeInTheDocument()
  })
})
