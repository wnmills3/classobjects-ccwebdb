import userEvent from '@testing-library/user-event'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    bulkEditInventory: vi.fn(),
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

beforeEach(() => {
  vi.resetAllMocks()
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.listSalesVenues.mockResolvedValue(VENUES)
  api.listLots.mockResolvedValue({ lots: [] })
})

describe('BulkEditBar', () => {
  it('offers to change items for sale only once the server names them', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.bulkEditInventory
      .mockRejectedValueOnce(
        new Error('For sale -- CC-000001: listing #3 at 189.00. A change shows ...'),
      )
      .mockResolvedValueOnce({ updated: 2 })
    render(<BulkEditBar ids={[1, 2]} onApplied={onApplied} onClear={vi.fn()} />)

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
      new Error('For sale -- CC-000001: listing #3 at 189.00. A change shows ...'),
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

  it('cannot offer a selection with nothing on this page', () => {
    render(<BulkEditBar ids={[5]} rows={ROWS} onApplied={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Offer for sale...' })).toBeDisabled()
  })

  // The brief for this task asserted one call --
  // `api.createLot({add_item_ids: [1, 2]})` -- and the API refuses that body:
  // `SalesLotIn` is `extra="forbid"` over `title` and `description` alone,
  // because "a lot begins assembling and empty; members are a PATCH"
  // (`routers/lots.create_sales_lot`). So a passing single-call assertion
  // would have pinned a request that is a 422 every time it is really sent.
  // Both calls are asserted instead, and `management/api.test.js` pins each body.
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
    // One off-page id read "and 1 more ... which are grouped too".
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
