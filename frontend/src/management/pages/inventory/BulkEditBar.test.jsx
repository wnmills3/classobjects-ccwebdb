import userEvent from '@testing-library/user-event'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    bulkEditInventory: vi.fn(),
    // The location picker's own call.
    listStorageLocations: vi.fn(),
    // OfferDialog's own calls, reachable once "Offer for sale..." is pressed.
    listSalesVenues: vi.fn(),
    getOfferTitles: vi.fn(),
    createOffers: vi.fn(),
    // And the lot calls, reachable once "Group into lot..." is pressed.
    listLots: vi.fn(),
    createLot: vi.fn(),
    updateLot: vi.fn(),
  },
}))

import { api } from '../../api'
import BulkEditBar from './BulkEditBar'
import { renderWithProviders } from '../../../test/helpers'

// What the inventory page has on screen: the current page of results. Only
// these rows can be priced, because only these have been loaded.
const ROWS = [
  {
    id: 1,
    item_code: 'CC-000001',
    source_title: '1935 Silver Certificate $1',
    description: 'Blue seal.',
    total_cost: '12.00',
  },
  {
    id: 2,
    item_code: 'CC-000002',
    source_title: '1957 Silver Certificate $1',
    description: 'Blue seal.',
    total_cost: '9.00',
  },
]

const VENUES = [
  {
    code: 'store',
    name: 'Web store',
    is_own_store: true,
    is_active: true,
    commission_rate: null,
    processing_rate: null,
    processing_fixed: null,
    listing_fee: null,
  },
]

/** The server's refusal of a change to items for sale: `for_sale` names them. */
const forSaleRefusal = (message) =>
  Object.assign(new Error(message), {
    status: 409,
    body: { detail: message, for_sale: ['CC-000001'] },
  })

beforeEach(() => {
  vi.resetAllMocks()
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.listSalesVenues.mockResolvedValue(VENUES)
  api.listLots.mockResolvedValue({ lots: [] })
  api.listStorageLocations.mockResolvedValue([
    { id: 7, label: 'First National, box 101' },
    { id: 9, label: 'Home safe' },
  ])
})

describe('BulkEditBar', () => {
  it('offers a note selection no Year, and starts it on another field', () => {
    render(
      <BulkEditBar view="currency" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    const options = [...screen.getByRole('combobox').options].map((o) => o.value)
    expect(options).not.toContain('year_start')
    expect(screen.getByRole('combobox')).toHaveValue(options[0])
  })

  it('offers to change items for sale only once the server names them', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.bulkEditInventory
      .mockRejectedValueOnce(
        forSaleRefusal(
          'For sale -- CC-000001: listing #3 at 189.00. A change shows ...',
        ),
      )
      .mockResolvedValueOnce({ updated: 2 })
    // The coin view: Year is a coin's field (a note's year is its series year).
    render(
      <BulkEditBar view="coins" ids={[1, 2]} onApplied={onApplied} onClear={vi.fn()} />,
    )

    expect(screen.queryByRole('checkbox')).toBeNull()
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], { year_start: 1964 })
    expect(await screen.findByText(/CC-000001/)).toBeVisible()

    await user.click(
      screen.getByRole('checkbox', { name: 'Change the items for sale too' }),
    )
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], {
      year_start: 1964,
      acknowledge_for_sale: true,
    })
    expect(onApplied).toHaveBeenCalled()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('shows its own heading but not the explanation the server message already gives', async () => {
    // The server's refusal already reads "A change shows to buyers at once" --
    // ForSaleNotice must not repeat that sentence when it has no `uses` of its
    // own to explain, or the operator sees it stacked twice.
    const user = userEvent.setup()
    api.bulkEditInventory.mockRejectedValueOnce(
      forSaleRefusal('For sale -- CC-000001: listing #3 at 189.00. A change shows ...'),
    )
    render(<BulkEditBar ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    await screen.findByText(/CC-000001/)

    const notice = screen.getByRole('alert')
    expect(notice).toHaveTextContent('Some of the selected items are for sale.')
    expect(notice).not.toHaveTextContent(
      'A change shows to buyers at once; each sale keeps the item as it was sold.',
    )
  })

  // The refusal is known by the `for_sale` its body carries: the wording of
  // a message is for people, and may change.
  it.each([
    [
      'offers the box for a refusal naming items for sale, whatever its words',
      forSaleRefusal('CC-000001 is offered: listing #3 at 189.00.'),
      true,
    ],
    [
      'offers no box for another 409 whose words begin the same way',
      Object.assign(new Error('For sale prices are being recalculated; try again.'), {
        status: 409,
        body: { detail: 'For sale prices are being recalculated; try again.' },
      }),
      false,
    ],
  ])('%s', async (_what, refusal, offered) => {
    const user = userEvent.setup()
    api.bulkEditInventory.mockRejectedValueOnce(refusal)
    render(
      <BulkEditBar view="coins" ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))

    expect(await screen.findByText(refusal.message)).toBeVisible()
    const box = screen.queryByRole('checkbox', {
      name: 'Change the items for sale too',
    })
    expect(box !== null).toBe(offered)
  })

  // Paper has no metal, and the API has no metal on the currency view.
  it('offers Metal on the coin view', () => {
    render(<BulkEditBar view="coins" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)
    const fields = Array.from(
      screen.getByRole('combobox').querySelectorAll('option'),
    ).map((o) => o.textContent)
    expect(fields).toContain('Metal')
  })

  it('does not offer Metal on the currency view', () => {
    render(
      <BulkEditBar view="currency" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    const fields = Array.from(
      screen.getByRole('combobox').querySelectorAll('option'),
    ).map((o) => o.textContent)
    expect(fields).not.toContain('Metal')
    expect(fields).toContain('Grade')
  })

  it('offers the selection for sale, and says which ids were offered', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    api.createOffers.mockResolvedValue({ listings: [{ id: 21 }, { id: 22 }] })
    render(
      <BulkEditBar
        ids={[1, 2]}
        rows={ROWS}
        onApplied={vi.fn()}
        onOffered={onOffered}
        onClear={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Offer for sale...' }))

    await screen.findByRole('option', { name: 'Web store' })
    await user.selectOptions(screen.getByLabelText('Platform'), 'store')
    await user.type(screen.getByLabelText('Price for CC-000001'), '24.00')
    await user.type(screen.getByLabelText('Price for CC-000002'), '18.00')
    await user.click(screen.getByRole('button', { name: 'Offer 2 for sale' }))

    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'store',
      format: 'fixed_price',
      items: [
        {
          item_id: 1,
          price: '24.00',
          title: '1935 Silver Certificate $1',
          description: 'Blue seal.',
          external_id: null,
        },
        {
          item_id: 2,
          price: '18.00',
          title: '1957 Silver Certificate $1',
          description: 'Blue seal.',
          external_id: null,
        },
      ],
    })
    // Exactly the ids that were offered, so the parent can take those out of
    // the selection and leave the rest of it alone.
    await waitFor(() => expect(onOffered).toHaveBeenCalledWith([1, 2]))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  // The other half of naming the off-page rows: they were not offered, so
  // they are still the operator's to deal with and must stay selected. The
  // bulk edit's own callback would have dropped the whole selection.
  it('leaves the ids it could not offer out of what it reports as offered', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    const onApplied = vi.fn()
    api.createOffers.mockResolvedValue({ listings: [{ id: 21 }, { id: 22 }] })
    render(
      <BulkEditBar
        ids={[1, 2, 5, 6]}
        rows={ROWS}
        onApplied={onApplied}
        onOffered={onOffered}
        onClear={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Offer for sale...' }))
    await screen.findByRole('option', { name: 'Web store' })
    await user.selectOptions(screen.getByLabelText('Platform'), 'store')
    await user.type(screen.getByLabelText('Price for CC-000001'), '24.00')
    await user.type(screen.getByLabelText('Price for CC-000002'), '18.00')
    await user.click(screen.getByRole('button', { name: 'Offer 2 for sale' }))

    await waitFor(() => expect(onOffered).toHaveBeenCalledWith([1, 2]))
    expect(onApplied).not.toHaveBeenCalled()
  })

  // A selection survives paging, and a row from another page has no price,
  // no title and no cost on screen. Dropping it quietly is how a batch
  // silently offers fewer items than the count on the button.
  it('says how many of the selection are not on this page', async () => {
    const user = userEvent.setup()
    render(
      <BulkEditBar
        ids={[1, 2, 5, 6]}
        rows={ROWS}
        onApplied={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Offer for sale...' }))
    expect(
      await screen.findByText(/2 other selected item\(s\) are not on this page/),
    ).toBeVisible()
  })

  it('says why it cannot offer a selection with nothing on this page', async () => {
    const user = userEvent.setup()
    render(<BulkEditBar ids={[5]} rows={ROWS} onApplied={vi.fn()} onClear={vi.fn()} />)
    const offer = screen.getByRole('button', { name: 'Offer for sale...' })
    expect(offer).toBeEnabled()
    await user.click(offer)
    expect(screen.getByText(/None of the selected items is on this page/)).toBeVisible()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says a value is wanted when Apply is pressed with none, and sends nothing', async () => {
    const user = userEvent.setup()
    render(
      <BulkEditBar view="coins" ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    const apply = screen.getByRole('button', { name: 'Apply to 2' })
    expect(apply).toBeEnabled()
    await user.click(apply)
    expect(screen.getByText('Type the new value first.')).toBeVisible()
    expect(api.bulkEditInventory).not.toHaveBeenCalled()
  })

  // Two calls, not one: `api.createLot({add_item_ids: [1, 2]})` is a body the
  // API refuses. `SalesLotIn` is `extra="forbid"` over `title` and
  // `description` alone -- a lot "begins `assembling` and empty; members are a
  // PATCH" -- so a single-call assertion would pin a request that is a 422
  // every time it is really sent. `management/api.test.js` pins each body.
  it('groups the selection into a lot', async () => {
    const user = userEvent.setup()
    api.createLot.mockResolvedValue({
      id: 5,
      title: 'Two silver certificates',
      version: 1,
      members: [],
    })
    api.updateLot.mockResolvedValue({
      id: 5,
      title: 'Two silver certificates',
      version: 2,
      members: [],
    })
    renderWithProviders(<BulkEditBar ids={[1, 2]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    await user.type(await screen.findByLabelText('Title'), 'Two silver certificates')
    await user.click(await screen.findByRole('button', { name: /^create lot$/i }))

    expect(api.createLot).toHaveBeenCalledWith({
      title: 'Two silver certificates',
      description: '',
    })
    expect(api.updateLot).toHaveBeenCalledWith(
      5,
      expect.objectContaining({ add_item_ids: [1, 2] }),
    )
  })

  it('adds the selection to a lot that is already assembling', async () => {
    // The other half of the dialog, and the one the Lots page relies on for
    // its own "add": a coin joins an existing lot with one PATCH and no new
    // lot at all. Without this case, a dialog that quietly started a second
    // lot named after the first would still pass everything above.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({
      lots: [{ id: 3, title: 'Three Morgans', version: 7, members: [] }],
    })
    api.updateLot.mockResolvedValue({
      id: 3,
      title: 'Three Morgans',
      version: 8,
      members: [],
    })
    renderWithProviders(<BulkEditBar ids={[1, 2]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    await user.selectOptions(await screen.findByLabelText('Lot'), '3')
    await user.click(await screen.findByRole('button', { name: /^add to lot$/i }))

    expect(api.createLot).not.toHaveBeenCalled()
    expect(api.updateLot).toHaveBeenCalledWith(3, {
      add_item_ids: [1, 2],
      version: 7,
    })
  })

  it('groups a selection that reaches past this page of results', async () => {
    // Grouping needs only ids, so the off-page half of a selection goes in
    // too. Offering cannot do that -- it needs a price and a cost basis per
    // item -- and copying its `chosen` rows here would have built a lot of
    // two out of a selection of four without saying so.
    const user = userEvent.setup()
    api.createLot.mockResolvedValue({ id: 5, title: 'Four', version: 1, members: [] })
    api.updateLot.mockResolvedValue({ id: 5, title: 'Four', version: 2, members: [] })
    renderWithProviders(<BulkEditBar ids={[1, 2, 5, 6]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    await user.type(await screen.findByLabelText('Title'), 'Four')
    await user.click(await screen.findByRole('button', { name: /^create lot$/i }))

    expect(api.updateLot).toHaveBeenCalledWith(
      5,
      expect.objectContaining({ add_item_ids: [1, 2, 5, 6] }),
    )
  })

  it('accounts for the selected rows it cannot name', async () => {
    // The heading counts the whole selection; the line under it can only
    // name the rows this page has loaded. "Group 4 item(s) into a lot" over
    // two codes reads as two, and the two it does not name are the ones the
    // operator cannot check.
    const user = userEvent.setup()
    renderWithProviders(<BulkEditBar ids={[1, 2, 5, 6]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    const dialog = await screen.findByRole('dialog', {
      name: 'Group 4 item(s) into a lot',
    })

    expect(
      within(dialog).getByText(
        /CC-000001, CC-000002 and 2 more not on this page, which are grouped too\./,
      ),
    ).toBeVisible()
  })

  it('says "which is" when exactly one selected row is off this page', async () => {
    // One off-page id must not read "and 1 more ... which are grouped too".
    const user = userEvent.setup()
    renderWithProviders(<BulkEditBar ids={[1, 2, 5]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    const dialog = await screen.findByRole('dialog')

    expect(
      within(dialog).getByText(
        /CC-000001, CC-000002 and 1 more not on this page, which is grouped too\./,
      ),
    ).toBeVisible()
  })

  it('names every selected row when they are all on this page', async () => {
    // The other side of it: no dangling "and 0 more".
    const user = userEvent.setup()
    renderWithProviders(<BulkEditBar ids={[1, 2]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText('CC-000001, CC-000002')).toBeVisible()
    expect(within(dialog).queryByText(/not on this page/)).toBeNull()
  })

  it('says the lot was started when only its coins were refused', async () => {
    // The PATCH is all or nothing, so a refusal leaves a lot that exists and
    // is empty. Reporting the bare refusal would send the operator looking
    // for a lot they had been told nothing about.
    const user = userEvent.setup()
    api.createLot.mockResolvedValue({ id: 5, title: 'Two', version: 1, members: [] })
    api.updateLot.mockRejectedValue(new Error('CC-000002 is already in a lot'))
    renderWithProviders(<BulkEditBar ids={[1, 2]} rows={ROWS} view="coins" />, {
      strict: true,
    })
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    await user.type(await screen.findByLabelText('Title'), 'Two')
    await user.click(await screen.findByRole('button', { name: /^create lot$/i }))

    expect(
      await screen.findByText(
        'Two was started but is empty: CC-000002 is already in a lot',
      ),
    ).toBeVisible()
  })

  it('fills the lot it started when asked again, and starts no second one', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    api.createLot.mockResolvedValue({ id: 5, title: 'Two', version: 1, members: [] })
    api.updateLot
      .mockRejectedValueOnce(new Error('CC-000002 is already in a lot'))
      .mockResolvedValueOnce({ id: 5, title: 'Two', version: 2 })
    renderWithProviders(
      <BulkEditBar ids={[1, 2]} rows={ROWS} view="coins" onClear={onClose} />,
    )
    await user.click(screen.getByRole('button', { name: /group into lot/i }))
    await user.type(await screen.findByLabelText('Title'), 'Two')
    await user.click(await screen.findByRole('button', { name: /^create lot$/i }))
    await screen.findByText(/Two was started but is empty/)

    await user.click(screen.getByRole('button', { name: /^create lot$/i }))

    expect(await screen.findByText('2 item(s) are in Two.')).toBeVisible()
    expect(api.createLot).toHaveBeenCalledTimes(1)
    expect(api.updateLot).toHaveBeenCalledTimes(2)
    expect(api.updateLot).toHaveBeenLastCalledWith(5, {
      add_item_ids: [1, 2],
      version: 1,
    })
  })

  it('asks again about items for sale once the selection is another one', async () => {
    const user = userEvent.setup()
    api.bulkEditInventory
      .mockRejectedValueOnce(
        forSaleRefusal('For sale -- CC-000001: listing #3 at 189.00.'),
      )
      .mockResolvedValueOnce({ updated: 3 })
    const bar = (ids) => (
      <BulkEditBar view="coins" ids={ids} onApplied={vi.fn()} onClear={vi.fn()} />
    )
    const { rerender } = render(bar([1, 2]))
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    await user.click(
      await screen.findByRole('checkbox', { name: 'Change the items for sale too' }),
    )

    // A third item joins: the tick was given for the two the server named.
    rerender(bar([1, 2, 3]))
    expect(screen.queryByRole('checkbox')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Apply to 3' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2, 3], {
      year_start: 1964,
    })
  })

  it('moves the selection to a storage location picked from the list', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.bulkEditInventory.mockResolvedValueOnce({ updated: 2 })
    render(
      <BulkEditBar
        view="currency"
        ids={[1, 2]}
        onApplied={onApplied}
        onClear={vi.fn()}
      />,
    )

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Field to change' }),
      'Location',
    )
    // A picker of the places there are, not a box to type an id into.
    expect(screen.queryByPlaceholderText('New value')).toBeNull()
    const apply = screen.getByRole('button', { name: 'Apply to 2' })
    await user.click(apply)
    expect(screen.getByText('Choose the location first.')).toBeVisible()
    expect(api.bulkEditInventory).not.toHaveBeenCalled()

    await user.selectOptions(
      await screen
        .findByRole('option', { name: 'Home safe' })
        .then((o) => o.closest('select')),
      'Home safe',
    )
    await user.click(apply)

    // The location's id, as a number: the schema takes no "9".
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], {
      storage_location_id: 9,
    })
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
  })

  it('forgets a value typed for one field when another is chosen', async () => {
    const user = userEvent.setup()
    render(<BulkEditBar view="coins" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('New value'), '1964')
    const field = screen.getByRole('combobox', { name: 'Field to change' })
    await user.selectOptions(field, 'Location')
    // 1964 is a year, not location number 1964: nothing is chosen yet.
    await user.click(screen.getByRole('button', { name: 'Apply to 1' }))
    expect(screen.getByText('Choose the location first.')).toBeVisible()

    await user.selectOptions(field, 'Grade')
    expect(screen.getByPlaceholderText('New value')).toHaveValue('')
    expect(api.bulkEditInventory).not.toHaveBeenCalled()
  })

  it('does not offer it for another refusal', async () => {
    const user = userEvent.setup()
    api.bulkEditInventory.mockRejectedValueOnce(new Error('Unknown grade: zz'))
    render(<BulkEditBar ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 1' }))
    expect(await screen.findByText('Unknown grade: zz')).toBeVisible()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})
