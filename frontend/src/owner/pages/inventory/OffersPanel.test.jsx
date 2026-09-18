import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listListings: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
    createOffers: vi.fn(),
  },
}))

import { api } from '../../api'
import OffersPanel from './OffersPanel'
import { renderWithProviders } from '../../../test/helpers'

const ITEM = {
  id: 7,
  item_code: 'CC-000007',
  source_title: '1881-S Morgan Dollar',
  description: 'Blast white.',
  total_cost: '120.00',
}

const EBAY = {
  id: 14,
  item_id: 7,
  item_code: 'CC-000007',
  item_title: '1881-S Morgan Dollar',
  venue: 'ebay',
  venue_name: 'eBay',
  format: 'fixed_price',
  status: 'active',
  price: '189.00',
  currency: 'USD',
  quantity_available: 1,
  title: '1881-S Morgan Dollar MS64',
  description: 'Blast white.',
  external_id: '1234567',
  external_url: 'https://www.ebay.com/itm/1234567',
  listed_at: '2026-09-17T12:00:00Z',
  ended_at: null,
  paused_by_listing_id: null,
  cost_basis: '120.00',
  version: 3,
}

// The same item's store listing, set aside while the eBay offer runs.
const STORE = {
  ...EBAY,
  id: 8,
  venue: 'store',
  venue_name: 'Web store',
  status: 'paused',
  price: '199.00',
  external_id: null,
  external_url: null,
  paused_by_listing_id: 14,
  version: 1,
}

// An offer that is over: history, with nothing left to do to it. On another
// platform, so each row of the fixture is told apart by its first cell.
const ENDED = {
  ...EBAY,
  id: 3,
  venue: 'whatnot',
  venue_name: 'Whatnot',
  status: 'ended',
  price: '175.00',
  ended_at: '2026-08-01T12:00:00Z',
  external_id: null,
  external_url: null,
}

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

const rowFor = (platform) =>
  screen.getByRole('row', { name: new RegExp(`^${platform}`) })

function renderPanel(options) {
  return renderWithProviders(<OffersPanel item={ITEM} />, options)
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listListings.mockResolvedValue([EBAY, STORE, ENDED])
  api.listSalesVenues.mockResolvedValue(VENUES)
  api.endListing.mockResolvedValue({ ...EBAY, status: 'ended' })
  api.createOffers.mockResolvedValue({ listings: [EBAY] })
})

describe('OffersPanel', () => {
  // Ended ones included: "where has this been offered before, and for how
  // much" is the question this panel answers beside the sale history.
  it('asks for every offer of this item, ended ones included', async () => {
    renderPanel()
    await screen.findByRole('row', { name: /^eBay/ })
    expect(api.listListings).toHaveBeenCalledWith({ item_id: 7, status: 'all' })
  })

  it('shows each offer with its platform, price and state', async () => {
    renderPanel()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    // Money exactly as it arrived, with the currency the API sent -- never
    // through Number(), and never labelled USD by this console.
    expect(within(row).getByText('189.00 USD')).toBeInTheDocument()
    expect(within(row).getByText('Active')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: '1234567' })).toHaveAttribute(
      'href',
      'https://www.ebay.com/itm/1234567',
    )
    expect(within(rowFor('Web store')).getByText('Paused')).toBeInTheDocument()
    const ended = rowFor('Whatnot')
    expect(within(ended).getByText('Ended')).toBeInTheDocument()
    expect(within(ended).getByText('175.00 USD')).toBeInTheDocument()
    // History: neither editable nor endable.
    expect(within(ended).queryByRole('button')).toBeNull()
  })

  // Ending is permanent and nothing resurrects the offer, so the click that
  // does it says which listing, on which platform, and what follows.
  it('asks before ending, naming the listing and the platform', async () => {
    const user = userEvent.setup()
    renderPanel()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName('End listing #14 for CC-000007 on eBay?')
    expect(within(dialog).getByText(/not recorded as sold/)).toBeVisible()
    expect(api.endListing).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'End listing' }))
    expect(api.endListing).toHaveBeenCalledWith(14)
    // Reloaded, not patched in place: ending an offer resumes the store
    // listing that was paused for it, and that row is on this very panel.
    await waitFor(() => expect(api.listListings).toHaveBeenCalledTimes(2))
  })

  it('ends nothing when the confirmation is dismissed', async () => {
    const user = userEvent.setup()
    renderPanel()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))
    await user.click(screen.getByRole('button', { name: 'Keep it offered' }))

    expect(api.endListing).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a refusal to end without losing the list', async () => {
    const user = userEvent.setup()
    api.endListing.mockRejectedValue(new Error('No such offer'))
    renderPanel()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))
    await user.click(screen.getByRole('button', { name: 'End listing' }))

    expect(await screen.findByText('No such offer')).toBeVisible()
    expect(rowFor('eBay')).toBeInTheDocument()
    expect(api.listListings).toHaveBeenCalledTimes(1)
  })

  // Nothing is offered while another listing holds the item: the writer
  // refuses a second offer, so the button that would start one is not there.
  it('does not offer to start an offer while one is running', async () => {
    renderPanel()
    await screen.findByRole('row', { name: /^eBay/ })
    expect(screen.queryByRole('button', { name: 'Offer for sale...' })).toBeNull()
  })

  it('offers an item that is not on offer anywhere', async () => {
    const user = userEvent.setup()
    api.listListings.mockResolvedValue([ENDED])
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'Offer for sale...' }))

    await screen.findByRole('option', { name: 'Web store' })
    await user.selectOptions(screen.getByLabelText('Platform'), 'store')
    await user.type(screen.getByLabelText('Price for CC-000007'), '199.00')
    await user.click(screen.getByRole('button', { name: 'Offer 1 for sale' }))

    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'store',
      format: 'fixed_price',
      items: [
        {
          item_id: 7,
          price: '199.00',
          title: '1881-S Morgan Dollar',
          description: 'Blast white.',
          external_id: null,
        },
      ],
    })
    // The new offer is on the server, not in this component's state.
    await waitFor(() => expect(api.listListings).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says so when the item has never been offered', async () => {
    api.listListings.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText('Not offered anywhere yet.')).toBeVisible()
  })

  // The list is beside the sale history, not instead of the form: a panel
  // that could not read its offers must not take the editor down with it.
  it('shows why the offers could not be read', async () => {
    api.listListings.mockRejectedValue(new Error('No such item'))
    renderPanel()
    expect(await screen.findByText('No such item')).toBeVisible()
  })
})
