import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listAuctions: vi.fn(),
    createAuction: vi.fn(),
    scheduleAuction: vi.fn(),
    consignAuction: vi.fn(),
    closeAuction: vi.fn(),
    cancelAuction: vi.fn(),
    addAuctionLot: vi.fn(),
    removeAuctionLot: vi.fn(),
    updateAuctionLot: vi.fn(),
    settleAuction: vi.fn(),
    listSalesVenues: vi.fn(),
    listStorageLocations: vi.fn(),
    listLots: vi.fn(),
    searchInventory: vi.fn(),
  },
}))

import { api } from '../api'
import Auctions from './Auctions'
import { ApiError } from '../../shared/api'
import { renderWithProviders } from '../../test/helpers'

const HERITAGE = { code: 'heritage', name: 'Heritage Auctions', kind: 'auction_house' }
const EBAY = { code: 'ebay', name: 'Weekly eBay', kind: 'marketplace' }

function listing(overrides = {}) {
  return {
    id: 101,
    item_id: null,
    item_code: null,
    item_title: '1881-S Morgan Dollar',
    venue: 'heritage',
    venue_name: 'Heritage Auctions',
    format: 'auction',
    status: 'active',
    price: '10.00',
    currency: 'USD',
    quantity_available: 1,
    title: '1881-S Morgan Dollar',
    description: '',
    external_id: null,
    external_url: null,
    listed_at: '2026-09-01T00:00:00Z',
    ended_at: null,
    paused_by_listing_id: null,
    sales_lot_id: 5,
    member_count: 1,
    cost_basis: '120.00',
    version: 1,
    ...overrides,
  }
}

function auctionLot(overrides = {}) {
  return {
    id: 201,
    lot_number: '1',
    reserve: '20.00',
    result: null,
    hammer_price: null,
    buyer: null,
    listing: listing(),
    ...overrides,
  }
}

function auction(overrides = {}) {
  return {
    id: 1,
    venue: 'heritage',
    venue_name: 'Heritage Auctions',
    title: 'September Signature Sale',
    external_id: 'SIG-2026-09',
    starts_at: null,
    ends_at: null,
    status: 'draft',
    consigned_on: null,
    notes: null,
    version: 1,
    lots: [],
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listSalesVenues.mockResolvedValue([HERITAGE, EBAY])
  api.listStorageLocations.mockResolvedValue([
    { id: 9, label: 'Home safe', kind: 'home' },
  ])
  api.listLots.mockResolvedValue({ lots: [] })
})

describe('Auctions', () => {
  it('lists auctions by platform, status and lot count', async () => {
    api.listAuctions.mockResolvedValue({
      auctions: [
        auction({ lots: [auctionLot(), auctionLot({ id: 202, lot_number: '2' })] }),
      ],
    })
    renderWithProviders(<Auctions />, { strict: true })
    expect(await screen.findByText('September Signature Sale')).toBeVisible()
    expect(screen.getByText('Heritage Auctions')).toBeVisible()
    expect(screen.getByText('Draft')).toBeVisible()
    expect(screen.getByText('2')).toBeVisible()
  })

  it('pages the list, saying where it is, with Newer and Older only where they lead', async () => {
    // Past one page, the rest is reached by Older: a list loaded unpaged
    // would show the first page alone and say nothing of the others.
    const user = userEvent.setup()
    const firstPage = Array.from({ length: 100 }, (_, n) =>
      auction({ id: 250 - n, title: `Sale ${250 - n}` }),
    )
    const lastPage = Array.from({ length: 50 }, (_, n) =>
      auction({ id: 150 - n, title: `Sale ${150 - n}` }),
    )
    api.listAuctions.mockImplementation(({ offset }) =>
      Promise.resolve({ auctions: offset ? lastPage : firstPage, total: 150 }),
    )
    renderWithProviders(<Auctions />, { strict: true })

    expect(await screen.findByText('1-100 of 150')).toBeVisible()
    expect(api.listAuctions).toHaveBeenCalledWith({ limit: 100, offset: 0 })
    expect(screen.queryByRole('button', { name: 'Newer' })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByText('101-150 of 150')).toBeVisible()
    expect(api.listAuctions).toHaveBeenLastCalledWith({ limit: 100, offset: 100 })
    expect(screen.getByText('Sale 101')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Older' })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Newer' }))
    expect(await screen.findByText('1-100 of 150')).toBeVisible()
  })

  it('counts a list that fits on one page as all of it, with nowhere to go', async () => {
    api.listAuctions.mockResolvedValue({ auctions: [auction()], total: 1 })
    renderWithProviders(<Auctions />, { strict: true })
    expect(await screen.findByText('1 of 1')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Newer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Older' })).toBeNull()
  })

  it('shows a load failure without blanking the page', async () => {
    // A load failure must show an error, not take the page down. The page's
    // own shell (its heading) has to survive too -- a render that replaced
    // the whole page with a bare error paragraph would still pass an
    // assertion on the error text alone. `Lots.jsx`'s identical-purpose test
    // asserts the same pairing.
    api.listAuctions.mockRejectedValue(new ApiError(500, 'boom', {}))
    renderWithProviders(<Auctions />, { strict: true })
    expect(await screen.findByText(/boom/)).toBeVisible()
    expect(screen.getByRole('heading', { name: /^auctions$/i })).toBeVisible()
  })

  it('starts a new auction on a chosen platform', async () => {
    const user = userEvent.setup()
    api.listAuctions.mockResolvedValue({ auctions: [] })
    api.createAuction.mockResolvedValue(auction({ id: 9 }))
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New auction...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new auction' })
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'heritage')
    await user.type(within(dialog).getByLabelText('Title'), 'September Signature Sale')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.createAuction).toHaveBeenCalledWith(
      expect.objectContaining({ venue: 'heritage', title: 'September Signature Sale' }),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('schedules a draft auction', async () => {
    const user = userEvent.setup()
    const row = auction()
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.scheduleAuction.mockResolvedValue({ ...row, status: 'scheduled', version: 2 })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Schedule' }))

    expect(api.scheduleAuction).toHaveBeenCalledWith(row.id)
    // The auction moved off draft: Schedule is no longer offered.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Schedule' })).toBeNull(),
    )
  })

  it('adds a single item as a lot of one, found by its item code', async () => {
    // The owner types the code the way every other item-selection
    // surface (OfferDialog, Lots, ItemFinder) shows it -- never a raw
    // database id -- and it is resolved through the same
    // `GET /api/inventory/{view}/search?item_code=...` the search page uses.
    const user = userEvent.setup()
    const row = auction()
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.searchInventory.mockResolvedValue({
      rows: [{ id: 42, item_code: 'CC-000042', description: 'A Morgan dollar' }],
    })
    api.addAuctionLot.mockResolvedValue({ ...row, lots: [auctionLot()] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Add lot...' }))
    const dialog = screen.getByRole('dialog', { name: /add a lot/i })
    await user.type(within(dialog).getByLabelText('Lot number'), '1')
    await user.type(within(dialog).getByLabelText('Item code'), 'CC-000042')
    await user.click(within(dialog).getByRole('button', { name: 'Add lot' }))

    expect(api.searchInventory).toHaveBeenCalledWith('coins', {
      item_code: 'CC-000042',
    })
    expect(api.addAuctionLot).toHaveBeenCalledWith(
      row.id,
      expect.objectContaining({ lot_number: '1', item_id: 42 }),
    )
  })

  it('falls back to the currency view when a code is not a coin', async () => {
    const user = userEvent.setup()
    const row = auction()
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve(
        view === 'currency'
          ? { rows: [{ id: 77, item_code: 'CC-000077', description: 'A banknote' }] }
          : { rows: [] },
      ),
    )
    api.addAuctionLot.mockResolvedValue({ ...row, lots: [auctionLot()] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Add lot...' }))
    const dialog = screen.getByRole('dialog', { name: /add a lot/i })
    await user.type(within(dialog).getByLabelText('Lot number'), '1')
    await user.type(within(dialog).getByLabelText('Item code'), 'CC-000077')
    await user.click(within(dialog).getByRole('button', { name: 'Add lot' }))

    expect(api.addAuctionLot).toHaveBeenCalledWith(
      row.id,
      expect.objectContaining({ item_id: 77 }),
    )
  })

  it('names the code when no item matches it, and does not add a lot', async () => {
    const user = userEvent.setup()
    const row = auction()
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.searchInventory.mockResolvedValue({ rows: [] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Add lot...' }))
    const dialog = screen.getByRole('dialog', { name: /add a lot/i })
    await user.type(within(dialog).getByLabelText('Lot number'), '1')
    await user.type(within(dialog).getByLabelText('Item code'), 'CC-999999')
    await user.click(within(dialog).getByRole('button', { name: 'Add lot' }))

    expect(
      await within(dialog).findByText('No item with code CC-999999.'),
    ).toBeVisible()
    expect(api.addAuctionLot).not.toHaveBeenCalled()
  })

  it('keeps each placeholder short enough to read, and says the date form in full', async () => {
    const user = userEvent.setup()
    api.listAuctions.mockResolvedValue({ auctions: [] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New auction...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new auction' })
    // A box here shows about twenty characters; a longer placeholder is cut.
    for (const box of dialog.querySelectorAll('[placeholder]')) {
      expect(box.getAttribute('placeholder').length).toBeLessThanOrEqual(20)
    }
    expect(within(dialog).getByLabelText('Starts')).toHaveAttribute(
      'placeholder',
      'date and time',
    )
    expect(within(dialog).getByLabelText('Ends')).toHaveAttribute(
      'placeholder',
      'date and time',
    )
    expect(
      within(dialog).getByText(
        'Starts and Ends are written year-month-day hour:minute, the hour out of 24.',
      ),
    ).toBeVisible()
  })

  it('offers a new auction only the platforms one can be run on', async () => {
    // The web store runs no auction, and a retired platform runs nothing.
    const user = userEvent.setup()
    api.listAuctions.mockResolvedValue({ auctions: [] })
    api.listSalesVenues.mockResolvedValue([
      { code: 'store', name: 'Web store', kind: 'own_store', is_own_store: true },
      { ...HERITAGE, is_own_store: false, is_active: true },
      { ...EBAY, is_own_store: false, is_active: true },
      {
        code: 'oldhouse',
        name: 'Old House',
        kind: 'auction_house',
        is_own_store: false,
        is_active: false,
      },
    ])
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New auction...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new auction' })
    await waitFor(() =>
      expect(
        within(within(dialog).getByLabelText('Platform'))
          .getAllByRole('option')
          .map((option) => option.textContent),
      ).toEqual(['Choose a platform', 'Heritage Auctions', 'Weekly eBay']),
    )
  })

  it("does not let a closed auction's lot numbers be edited", async () => {
    // `app.auctions.refuse_unless_lot_editable`: draft, scheduled or
    // consigned only. The console must not let the owner walk into that
    // refusal blindly.
    const row = auction({ status: 'closed', lots: [auctionLot()] })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    renderWithProviders(<Auctions />, { strict: true })

    await userEvent.setup().click(await screen.findByRole('button', { name: 'View' }))
    expect(screen.getByLabelText(/lot number for/i)).toBeDisabled()
  })

  it("saves a lot with the auction's version, and the next save with the new one", async () => {
    // A lot row has no version of its own; the auction's stands for it, and
    // each lot save moves it. The saved auction replaces the row, so a second
    // save from the same page carries the version the first one produced.
    const user = userEvent.setup()
    const row = auction({ status: 'scheduled', version: 3, lots: [auctionLot()] })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.updateAuctionLot
      .mockResolvedValueOnce({
        ...row,
        version: 4,
        lots: [auctionLot({ reserve: '25.00' })],
      })
      .mockResolvedValueOnce({
        ...row,
        version: 5,
        lots: [auctionLot({ lot_number: '1A', reserve: '25.00' })],
      })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    const reserve = screen.getByLabelText(/reserve for/i)
    await user.clear(reserve)
    await user.type(reserve, '25.00')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.updateAuctionLot).toHaveBeenLastCalledWith(row.id, 201, {
      lot_number: '1',
      reserve: '25.00',
      version: 3,
    })
    expect(await screen.findByText('Lot 1 saved.')).toBeVisible()

    const number = screen.getByLabelText(/lot number for/i)
    await user.clear(number)
    await user.type(number, '1A')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.updateAuctionLot).toHaveBeenLastCalledWith(row.id, 201, {
      lot_number: '1A',
      reserve: '25.00',
      version: 4,
    })
  })

  it('requires a return location to remove a lot from a consigned auction', async () => {
    const user = userEvent.setup()
    const row = auction({
      status: 'consigned',
      consigned_on: '2026-10-01',
      lots: [auctionLot()],
    })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.removeAuctionLot.mockResolvedValue({ ...row, lots: [] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: /^remove/i }))
    const dialog = screen.getByRole('dialog', { name: /remove lot/i })
    const confirm = within(dialog).getByRole('button', { name: 'Remove lot' })
    // Live, not greyed out: a press with no location sends nothing and says
    // what is missing.
    expect(confirm).toBeEnabled()
    const reason = 'Choose where the items come back to first.'
    expect(within(dialog).getByText(reason)).toBeVisible()
    await user.click(confirm)
    expect(api.removeAuctionLot).not.toHaveBeenCalled()
    expect(within(dialog).getByText(reason)).toHaveClass('error')

    const picker = within(dialog).getByLabelText(/return items to/i)
    // A place not yet recorded can be added from the picker itself.
    expect(
      within(picker).getByRole('option', { name: '+ Add a location...' }),
    ).toBeInTheDocument()
    await waitFor(() => expect(within(picker).getAllByRole('option')).toHaveLength(3))
    await user.selectOptions(picker, '9')
    expect(within(dialog).queryByText(reason)).toBeNull()
    await user.click(confirm)

    expect(api.removeAuctionLot).toHaveBeenCalledWith(row.id, row.lots[0].id, 9)
  })

  it('requires a return location to cancel a consigned auction', async () => {
    const user = userEvent.setup()
    const row = auction({ status: 'consigned', consigned_on: '2026-10-01' })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.cancelAuction.mockResolvedValue({ ...row, status: 'cancelled', lots: [] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Cancel...' }))
    const dialog = screen.getByRole('dialog', { name: /cancel september/i })
    const confirm = within(dialog).getByRole('button', { name: 'Cancel auction' })
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    expect(api.cancelAuction).not.toHaveBeenCalled()
    expect(
      within(dialog).getByText('Choose where the items come back to first.'),
    ).toHaveClass('error')

    await user.selectOptions(within(dialog).getByLabelText(/return items to/i), '9')
    await user.click(confirm)

    expect(api.cancelAuction).toHaveBeenCalledWith(
      row.id,
      expect.objectContaining({ returned_to_location_id: 9 }),
    )
  })

  it('shows the settlement grid only once the auction is closed', async () => {
    const row = auction({ status: 'consigned', lots: [auctionLot()] })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    renderWithProviders(<Auctions />, { strict: true })

    await userEvent.setup().click(await screen.findByRole('button', { name: 'View' }))
    expect(screen.queryByRole('button', { name: /^settle/i })).toBeNull()
  })
})
