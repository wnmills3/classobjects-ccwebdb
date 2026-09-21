import userEvent from '@testing-library/user-event'
import { act, screen, waitFor, within } from '@testing-library/react'
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

// The shop and one outside platform. `is_own_store` is the difference that
// decides whether an item already on offer may be offered again, and it is
// on the platform, not on the listing.
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
  {
    code: 'ebay',
    name: 'eBay',
    is_own_store: false,
    is_active: true,
    commission_rate: '0.1325',
    processing_rate: '0.0290',
    processing_fixed: '0.30',
    listing_fee: null,
  },
]

const rowFor = (platform) =>
  screen.getByRole('row', { name: new RegExp(`^${platform}`) })

function renderPanel(options = {}) {
  const { onChanged = vi.fn(), ...rest } = options
  return renderWithProviders(<OffersPanel item={ITEM} onChanged={onChanged} />, rest)
}

const offerButton = () => screen.queryByRole('button', { name: 'Offer for sale...' })

/**
 * Render with the platform list held back, and hand it over on demand.
 *
 * Whether the button shows depends on which platforms are the shop, and that
 * arrives in its own request. Resolving it by hand is what makes "the button
 * is still there" mean "still there once everything is known" rather than
 * "still there because nothing has come back yet".
 */
function withHeldBackVenues() {
  let hand
  api.listSalesVenues.mockReturnValue(
    new Promise((resolve) => {
      hand = resolve
    }),
  )
  return async (venues = VENUES) => {
    await act(async () => {
      hand(venues)
    })
  }
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

  // An item held on another platform is the one refusal that is not worth
  // walking into: `offering_writes.offer` answers "is active on eBay, listing
  // #14: end it first" and there is nothing to do about it here.
  it('does not offer to start an offer while another platform holds the item', async () => {
    const handOverVenues = withHeldBackVenues()
    renderPanel()
    await screen.findByRole('row', { name: /^eBay/ })
    await handOverVenues()

    expect(offerButton()).toBeNull()
  })

  // The case the spec designs (`docs/specs/selling-design.md`): an item
  // active in the shop is offered elsewhere in ONE step, and its store
  // listing is paused rather than destroyed. A panel that hid the button
  // here would make that move an End followed by an Offer, throwing away the
  // listing `paused_by_listing_id` exists to bring back.
  it('offers an item that only the web store is holding', async () => {
    const handOverVenues = withHeldBackVenues()
    api.listListings.mockResolvedValue([{ ...STORE, status: 'active' }])
    renderPanel()
    await screen.findByRole('row', { name: /^Web store/ })
    await handOverVenues()

    expect(offerButton()).toBeVisible()
  })

  // Offering a shop item in the shop again IS refused -- by the server, per
  // item, in the 409 the dialog shows in place with the prices still typed.
  // Until the platforms are known, nothing is known to hold it elsewhere.
  it('offers while it does not yet know which platforms are the shop', async () => {
    withHeldBackVenues()
    renderPanel()
    await screen.findByRole('row', { name: /^eBay/ })

    expect(offerButton()).toBeVisible()
  })

  it('says so when the platforms cannot be read, rather than hiding the button', async () => {
    api.listSalesVenues.mockRejectedValue(new Error('No platforms'))
    renderPanel()
    await screen.findByRole('row', { name: /^eBay/ })

    expect(await screen.findByText('No platforms')).toBeVisible()
    expect(offerButton()).toBeVisible()
  })

  // Both writes change what the server says about the item -- whether it is
  // for sale -- and the editor above this panel has to be told.
  it('tells the editor after an offer is ended', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    renderPanel({ onChanged })
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))
    await user.click(screen.getByRole('button', { name: 'End listing' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('does not tell the editor about an end the server refused', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    api.endListing.mockRejectedValue(new Error('No such offer'))
    renderPanel({ onChanged })
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))
    await user.click(screen.getByRole('button', { name: 'End listing' }))

    expect(await screen.findByText('No such offer')).toBeVisible()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('offers an item that is not on offer anywhere', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    api.listListings.mockResolvedValue([ENDED])
    renderPanel({ onChanged })
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
    // And the editor above is told, because the item is for sale now.
    expect(onChanged).toHaveBeenCalled()
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

  // A lot this coin is inside, offered in the WEB STORE -- the shop on
  // purpose. A lot on an outside platform is already hidden by the
  // `heldElsewhere` rule, so only a shop lot can tell "a lot holds this
  // coin" apart from "another platform holds it"; a fixture on eBay would
  // pass against a panel that never learned about lots at all.
  //
  // `item_code` null and `sales_lot_id` set is the real shape of the row:
  // `ck_listing_item_xor_lot` makes a listing name an item or a lot, never
  // both, and `member_count` is what the API sends so this needs no second
  // request.
  const SHOP_LOT = {
    id: 21,
    item_id: null,
    item_code: null,
    item_title: 'Three Morgan Dollars',
    venue: 'store',
    venue_name: 'Web store',
    format: 'fixed_price',
    status: 'active',
    price: '1000.00',
    currency: 'USD',
    quantity_available: 1,
    title: 'Three Morgan Dollars',
    description: '',
    external_id: null,
    external_url: null,
    listed_at: '2026-09-18T12:00:00Z',
    ended_at: null,
    paused_by_listing_id: null,
    sales_lot_id: 3,
    member_count: 3,
    cost_basis: '650.00',
    version: 1,
  }

  // The row says what is being sold for 1000.00, and it is not this coin.
  // Without the subject cell every row on this panel was silently "the item
  // you are looking at", which is how a lot's price reads as a coin's.
  it('names the lot a coin is offered inside, rather than the coin', async () => {
    api.listListings.mockResolvedValue([SHOP_LOT])
    renderPanel({ strict: true })

    const row = await screen.findByRole('row', { name: /^Web store/ })
    expect(within(row).getByText('Three Morgan Dollars (3 items)')).toBeInTheDocument()
    // The coin's own code must not appear on a row that is not about it.
    expect(within(row).queryByText('CC-000007')).toBeNull()
    expect(within(row).getByText('1000.00 USD')).toBeInTheDocument()
  })

  // `offering_writes._refuse_grouped` turns this offer down -- "is in lot
  // #3, which is offered" -- so a button here only teaches the owner to
  // click through a refusal. The shop is not an exception to that rule the
  // way it is to the platform rule above.
  it('does not offer to start an offer for a coin inside an offered lot', async () => {
    api.listListings.mockResolvedValue([SHOP_LOT])
    renderPanel({ strict: true })

    await screen.findByRole('row', { name: /^Web store/ })
    expect(offerButton()).toBeNull()
  })

  // The other half of the same rule, and the reason it asks about status
  // rather than only about `sales_lot_id`: a dissolved lot is history, it
  // holds nothing, and the coin is offerable again. A rule written without
  // the status check would lock this coin out of being sold forever.
  it('offers a coin whose lot has ended, which holds it no longer', async () => {
    api.listListings.mockResolvedValue([
      { ...SHOP_LOT, status: 'ended', ended_at: '2026-09-19T12:00:00Z' },
    ])
    renderPanel({ strict: true })

    await screen.findByRole('row', { name: /^Web store/ })
    expect(offerButton()).toBeInTheDocument()
  })
})
