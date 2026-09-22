import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listAuctions: vi.fn(),
    createAuction: vi.fn(),
    updateAuction: vi.fn(),
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

  it('shows a load failure without blanking the page', async () => {
    // Per 872e219: a load failure must show an error, not take the page down.
    api.listAuctions.mockRejectedValue(new ApiError(500, 'boom', {}))
    renderWithProviders(<Auctions />, { strict: true })
    expect(await screen.findByText(/boom/)).toBeVisible()
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

  it('adds a single item as a lot of one', async () => {
    const user = userEvent.setup()
    const row = auction()
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    api.addAuctionLot.mockResolvedValue({ ...row, lots: [auctionLot()] })
    renderWithProviders(<Auctions />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Add lot...' }))
    const dialog = screen.getByRole('dialog', { name: /add a lot/i })
    await user.type(within(dialog).getByLabelText('Lot number'), '1')
    await user.type(within(dialog).getByLabelText('Item ID'), '42')
    await user.click(within(dialog).getByRole('button', { name: 'Add lot' }))

    expect(api.addAuctionLot).toHaveBeenCalledWith(
      row.id,
      expect.objectContaining({ lot_number: '1', item_id: 42 }),
    )
  })

  it('does not let a closed auction s lot numbers be edited', async () => {
    // `app.auctions.refuse_unless_lot_editable`: draft, scheduled or
    // consigned only. The console must not let the owner walk into that
    // refusal blindly.
    const row = auction({ status: 'closed', lots: [auctionLot()] })
    api.listAuctions.mockResolvedValue({ auctions: [row] })
    renderWithProviders(<Auctions />, { strict: true })

    await userEvent.setup().click(await screen.findByRole('button', { name: 'View' }))
    expect(screen.getByLabelText(/lot number for/i)).toBeDisabled()
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
    expect(confirm).toBeDisabled()

    await user.selectOptions(within(dialog).getByLabelText(/return items to/i), '9')
    expect(confirm).toBeEnabled()
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
    expect(confirm).toBeDisabled()

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
