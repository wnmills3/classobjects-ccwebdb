import userEvent from '@testing-library/user-event'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listListings: vi.fn(),
    updateListing: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
    recordSale: vi.fn(),
  },
}))

import { api } from '../api'
import Listings from './Listings'
import { marginPercent } from './platform-rates'
import { subjectOf } from './listing-labels'
import { date } from '../../shared/format'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

// The offer that holds the item: on eBay, active, with a listing number.
const EBAY = {
  id: 14,
  item_id: 7,
  item_code: 'C-0007',
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

// The same item's store listing, set aside while the eBay offer runs. It is
// the *same* item by definition -- that is what pausing means -- so the two
// rows are told apart by their platform, which is the first cell.
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

// The same coin consigned to an auction house. Its platform kind is what
// `Listings.jsx` looks up to tell `RecordSaleDialog` a blank buyer means this
// house's undisclosed buyer -- the only row shape that exercises that lookup.
const HERITAGE = {
  ...EBAY,
  id: 22,
  item_id: 9,
  item_code: 'C-0009',
  item_title: '1893-S Morgan Dollar',
  venue: 'heritage',
  venue_name: 'Heritage',
  price: '2450.00',
  title: '1893-S Morgan Dollar VF20',
  external_id: '7654321',
  external_url: null,
  version: 2,
}

// A store listing that is genuinely on offer rather than set aside. This is
// the row that used to carry a Record sale button, which would have sold a
// shop item past the cart and past checkout.
const ACTIVE_STORE = {
  ...STORE,
  id: 31,
  status: 'active',
  paused_by_listing_id: null,
  version: 4,
}

// A LOT offered on eBay: three coins sold as one thing. `item_id` and
// `item_code` are null -- a lot is not an item and has no code -- and
// `item_title` carries the lot's own title, with `member_count` beside it.
// `ck_listing_item_xor_lot` is what makes those two shapes exclusive.
const LOT = {
  ...EBAY,
  id: 41,
  item_id: null,
  item_code: null,
  item_title: 'Three Morgans',
  sales_lot_id: 4,
  member_count: 3,
  price: '1000.00',
  title: 'Three Morgan Dollars, 1881-1883',
  description: 'A short date run.',
  external_id: null,
  external_url: null,
  cost_basis: '600.00',
  version: 1,
}

const VENUES = [
  {
    code: 'store',
    name: 'Web store',
    kind: 'own_store',
    is_own_store: true,
    is_active: true,
  },
  {
    code: 'ebay',
    name: 'eBay',
    kind: 'marketplace',
    is_own_store: false,
    is_active: true,
  },
  {
    code: 'heritage',
    name: 'Heritage',
    kind: 'auction_house',
    is_own_store: false,
    is_active: true,
  },
]

const FEE_KINDS = [{ code: 'commission', label: 'Commission', is_active: true }]

const STALE = 'This offer was changed by someone else. Reload and reapply your changes.'

/** The row for one platform. Anchored: "eBay" also appears inside the paused
 * row's explanation ("paused for listing #14 on eBay"), and the platform is
 * the first cell, so only an anchored match picks out the right row. */
const rowFor = (platform) =>
  screen.getByRole('row', { name: new RegExp(`^${platform}`) })

const feeKindsReference = emptyReference({
  tables: { sales_fee_kind: FEE_KINDS },
})

function renderPage(options = {}) {
  return renderWithProviders(<Listings />, {
    auth: adminAuth(),
    reference: feeKindsReference,
    ...options,
  })
}

async function openEditor(user) {
  const row = await screen.findByRole('row', { name: /^eBay/ })
  await user.click(within(row).getByRole('button', { name: 'Edit' }))
  return screen.getByRole('dialog', { name: 'Edit C-0007 on eBay' })
}

/** End the offer on a row, through the confirmation that now guards it. */
async function endOffer(user, row) {
  await user.click(within(row).getByRole('button', { name: 'End' }))
  const dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: 'End listing' }))
}

// `resetAllMocks`, not `clearAllMocks`: clearing calls `mockClear`, which
// leaves a queue of `mockResolvedValueOnce` values in place. One test that
// queues two and fails before consuming the second hands it to the NEXT test,
// whose first load then silently returns the wrong rows.
beforeEach(() => {
  vi.resetAllMocks()
  api.listListings.mockResolvedValue([EBAY, STORE])
  api.listSalesVenues.mockResolvedValue(VENUES)
})

describe('Listings', () => {
  it('lists an offer with its platform, money, margin and link', async () => {
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    expect(within(row).getByText('C-0007')).toBeInTheDocument()
    expect(within(row).getByText('1881-S Morgan Dollar MS64')).toBeInTheDocument()
    // The price as it arrived, not through a number: money crosses the API as
    // a decimal string and a float cannot hold cents exactly.
    expect(within(row).getByText('189.00 USD')).toBeInTheDocument()
    expect(within(row).getByText('120.00')).toBeInTheDocument()
    expect(within(row).getByText('36.5%')).toBeInTheDocument()
    expect(within(row).getByText('Active')).toBeInTheDocument()
    expect(within(row).getByText(date(EBAY.listed_at))).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: '1234567' })).toHaveAttribute(
      'href',
      'https://www.ebay.com/itm/1234567',
    )
  })

  // An item whose cost nobody has recorded has no margin, and saying "0%"
  // would read as a sale at cost -- a different fact entirely.
  it('shows no cost or margin when the cost basis is unknown', async () => {
    api.listListings.mockResolvedValue([{ ...EBAY, cost_basis: null }])
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    expect(within(row).getAllByText('--')).toHaveLength(2)
  })

  it('says what paused a row and does not offer to edit it', async () => {
    renderPage()
    const row = await screen.findByRole('row', { name: /^Web store/ })
    expect(within(row).getByText('paused for listing #14 on eBay')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  // The listing that paused this one is not always on screen -- a filter can
  // exclude it. Naming the listing without its platform is the honest answer;
  // guessing a platform would be a silent default.
  it('still names the listing that paused a row when that row is filtered out', async () => {
    api.listListings.mockResolvedValue([STORE])
    renderPage()
    const row = await screen.findByRole('row', { name: /^Web store/ })
    expect(within(row).getByText('paused for listing #14')).toBeInTheDocument()
  })

  it('filters by platform, format and status', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /^eBay/ })
    // Exact: the console's `query()` drops empty values, so these three keys
    // are the whole query string the API is asked for.
    expect(api.listListings).toHaveBeenCalledWith({
      venue: '',
      format: '',
      status: '',
    })

    await user.selectOptions(screen.getByLabelText('Platform'), 'ebay')
    await user.selectOptions(screen.getByLabelText('Format'), 'auction')
    await user.selectOptions(screen.getByLabelText('Status'), 'all')

    await waitFor(() =>
      expect(api.listListings).toHaveBeenLastCalledWith({
        venue: 'ebay',
        format: 'auction',
        status: 'all',
      }),
    )
  })

  it('edits an offer, sending its version as a number', async () => {
    const user = userEvent.setup()
    api.updateListing.mockResolvedValue({ ...EBAY, price: '175.00', version: 4 })
    renderPage()
    const dialog = await openEditor(user)
    const price = within(dialog).getByLabelText('Price')
    await user.clear(price)
    await user.type(price, '175.00')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    // Exact, not objectContaining: ListingUpdate forbids extras, and `version`
    // is an int -- Pydantic v2 will not coerce "3", so a version sent as a
    // string is a 422 that a loose assertion cannot see.
    expect(api.updateListing).toHaveBeenCalledWith(14, {
      price: '175.00',
      title: '1881-S Morgan Dollar MS64',
      description: 'Blast white.',
      external_id: '1234567',
      version: 3,
    })
    expect(await screen.findByText('175.00 USD')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('sends a blank listing number as null, not as an empty string', async () => {
    const user = userEvent.setup()
    api.updateListing.mockResolvedValue(EBAY)
    renderPage()
    const dialog = await openEditor(user)
    await user.clear(within(dialog).getByLabelText('Listing number'))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.updateListing).toHaveBeenCalledWith(14, {
      price: '189.00',
      title: '1881-S Morgan Dollar MS64',
      description: 'Blast white.',
      external_id: null,
      version: 3,
    })
  })

  it('saves with Ctrl+S', async () => {
    const user = userEvent.setup()
    api.updateListing.mockResolvedValue(EBAY)
    renderPage()
    await openEditor(user)
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    // The whole payload, like its neighbours: the API is mocked here, so a
    // shortcut that saved a different body than the button does would pass a
    // bare "was it called" every time.
    await waitFor(() =>
      expect(api.updateListing).toHaveBeenCalledWith(14, {
        price: '189.00',
        title: '1881-S Morgan Dollar MS64',
        description: 'Blast white.',
        external_id: '1234567',
        version: 3,
      }),
    )
  })

  // Read off the rendered dialog rather than the component's letter table: a
  // table can agree with itself while a control is left without its attribute
  // or given one twice.
  it('gives the edit window unique, unreserved access keys', async () => {
    const user = userEvent.setup()
    renderPage()
    const dialog = await openEditor(user)
    const letters = [...dialog.querySelectorAll('[accesskey]')].map((el) =>
      el.getAttribute('accesskey'),
    )
    expect(letters).toHaveLength(5)
    expect(new Set(letters).size).toBe(letters.length)
    // Chrome and Edge keep D, E and F for the address bar and menus.
    expect(letters.filter((l) => 'def'.includes(l))).toEqual([])
  })

  it('keeps the listings when only the platform list fails to load', async () => {
    // Two independent loads share one `error`. The platforms are needed for
    // the filter dropdown and nothing else, so losing them is no reason to
    // withhold the listings -- but the page used to return the error instead
    // of itself, and a failure in the lesser of the two blanked the whole
    // page. The rows are what the operator came for.
    api.listSalesVenues.mockRejectedValue(new Error('cannot load platforms'))
    renderPage()

    expect(await screen.findByText('cannot load platforms')).toBeInTheDocument()
    expect(await screen.findByRole('row', { name: /^eBay/ })).toBeInTheDocument()
  })

  it('shows a refusal in place and keeps the form open', async () => {
    const user = userEvent.setup()
    api.updateListing.mockRejectedValue(new Error(STALE))
    renderPage()
    const dialog = await openEditor(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await within(dialog).findByText(/changed by someone else/)).toBeVisible()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  // A blank price is a 422 from Pydantic, which reads as a schema complaint
  // rather than as the mistake it is. The form says so itself and sends
  // nothing, so nothing has to be undone.
  it('refuses a price that is not an amount without calling the API', async () => {
    const user = userEvent.setup()
    renderPage()
    const dialog = await openEditor(user)
    await user.clear(within(dialog).getByLabelText('Price'))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.updateListing).not.toHaveBeenCalled()
    expect(within(dialog).getByText(/amount like 189\.00/)).toBeVisible()
  })

  // Ending is permanent, and on a paused row it destroys a store listing
  // whose only other action was deliberately removed. It says what it will
  // do first: which listing, on which platform, and what follows.
  it('asks before ending, naming the listing and the platform', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName('End listing #14 for C-0007 on eBay?')
    expect(within(dialog).getByText(/not recorded as sold/)).toBeVisible()
    expect(api.endListing).not.toHaveBeenCalled()
  })

  // A paused row IS the store listing set aside for the eBay offer, not an
  // offer with a store listing to resume -- so the wording must not promise a
  // resume that will not happen, and must say the eBay offer is untouched.
  it('asks a different question before ending a paused row', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /^Web store/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName('End listing #8 for C-0007 on Web store?')
    expect(within(dialog).getByText(/not recorded as sold/)).toBeVisible()
    expect(
      within(dialog).getByText(
        /It was set aside for an offer elsewhere, and that offer is not affected by ending this one\./,
      ),
    ).toBeVisible()
    expect(within(dialog).queryByText(/goes back on sale/)).toBeNull()
    expect(api.endListing).not.toHaveBeenCalled()
  })

  it('ends nothing when the confirmation is dismissed', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'End' }))
    await user.click(screen.getByRole('button', { name: 'Keep it offered' }))

    expect(api.endListing).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(rowFor('eBay')).toBeInTheDocument()
  })

  it('ends an offer and reloads, because ending one can resume another', async () => {
    const user = userEvent.setup()
    api.endListing.mockResolvedValue({ ...EBAY, status: 'ended' })
    // The last arm is not `...Once`: a queued value that this test fails
    // before consuming would outlive it and answer the next test's first load.
    api.listListings
      .mockResolvedValueOnce([EBAY, STORE])
      .mockResolvedValue([{ ...STORE, status: 'active', paused_by_listing_id: null }])
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await endOffer(user, row)

    expect(api.endListing).toHaveBeenCalledWith(14)
    // The reload is the point: the store listing's status changed on the
    // server, and no edit to the ended row could have shown that.
    await waitFor(() => expect(api.listListings).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(within(rowFor('Web store')).getByText('Active')).toBeVisible(),
    )
    expect(screen.queryByRole('row', { name: /^eBay/ })).toBeNull()
  })

  // Record sale both creates the order and ends the listing
  // (`sales_writes.record_sale`), so it is offered only where End is safe to
  // offer in the sense that matters here: a row still actually on offer.
  // `record_sale` itself refuses anything but `active` with "not on offer",
  // so a paused row -- a store listing set aside, not a thing that just sold
  // there -- gets no button either.
  it('offers Record sale only on an active row', async () => {
    renderPage()
    const active = await screen.findByRole('row', { name: /^eBay/ })
    const paused = await screen.findByRole('row', { name: /^Web store/ })
    expect(
      within(active).getByRole('button', { name: 'Record sale…' }),
    ).toBeInTheDocument()
    expect(within(paused).queryByRole('button', { name: 'Record sale…' })).toBeNull()
  })

  // Not on the store's own rows, however active they are. `record_sale` maps
  // `own_store` to `paid` and would happily take one -- selling a shop item
  // past the cart, past checkout, and minting an "Undisclosed buyer (store)"
  // if the username were left blank. Whether that is wanted for an in-person
  // or show sale is the owner's decision, open in
  // `docs/specs/selling-design.md`; the button is not offered until it is made.
  it('does not offer Record sale on the store platform', async () => {
    api.listListings.mockResolvedValue([EBAY, ACTIVE_STORE])
    renderPage()
    const store = await screen.findByRole('row', { name: /^Web store/ })
    expect(within(store).queryByRole('button', { name: 'Record sale…' })).toBeNull()
    // The eBay row beside it, equally active, still has one -- so this is
    // about the platform and not about the button vanishing everywhere.
    expect(
      within(rowFor('eBay')).getByRole('button', { name: 'Record sale…' }),
    ).toBeInTheDocument()
  })

  // The `venues.find(...)?.kind === 'auction_house'` lookup this page does for
  // `RecordSaleDialog` had no test opening the dialog on such a row, so the
  // lookup could have returned anything.
  it('tells an auction house row that a blank buyer is the undisclosed one', async () => {
    const user = userEvent.setup()
    api.listListings.mockResolvedValue([HERITAGE])
    renderPage()
    const row = await screen.findByRole('row', { name: /^Heritage/ })
    await user.click(within(row).getByRole('button', { name: 'Record sale…' }))
    const dialog = screen.getByRole('dialog', {
      name: 'Record sale of C-0009 on Heritage',
    })
    expect(within(dialog).getByText(/Heritage's undisclosed buyer/)).toBeVisible()
  })

  // The other side of the same lookup: a marketplace row must not be told
  // anything about an undisclosed buyer, or the wording above would be what
  // every platform says and the lookup would be doing nothing.
  it('tells a marketplace row only that the platform may not name a buyer', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Record sale…' }))
    const dialog = screen.getByRole('dialog', { name: 'Record sale of C-0007 on eBay' })
    expect(
      within(dialog).getByText(
        'Leave the buyer blank if the platform does not name who bought it.',
      ),
    ).toBeVisible()
    expect(within(dialog).queryByText(/undisclosed buyer/)).toBeNull()
  })

  it('records a sale, then reloads and announces what was recorded', async () => {
    const user = userEvent.setup()
    api.recordSale.mockResolvedValue({
      id: 5,
      external_order_id: null,
      total_amount: '115.00',
      fee_total: '20.35',
      net_amount: '94.65',
      buyer: 'coinfan88',
      item_codes: ['C-0007'],
    })
    // The last arm is not `...Once`, for the same reason `end`'s test
    // avoids it: a queued value a failed test never consumed would outlive
    // it and answer the next test's first load.
    api.listListings
      .mockResolvedValueOnce([EBAY, STORE])
      .mockResolvedValue([{ ...EBAY, status: 'ended' }, STORE])
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Record sale…' }))
    const dialog = screen.getByRole('dialog', { name: 'Record sale of C-0007 on eBay' })
    await user.type(within(dialog).getByLabelText('Sale price'), '115.00')
    await user.type(within(dialog).getByLabelText('Commission'), '20.35')
    await user.click(within(dialog).getByRole('button', { name: 'Record sale' }))

    expect(api.recordSale).toHaveBeenCalledWith(14, {
      price: '115.00',
      buyer_username: null,
      external_order_id: null,
      fees: [{ kind: 'commission', amount: '20.35' }],
      equal_shares: false,
    })
    expect(screen.queryByRole('dialog')).toBeNull()
    // The reload is the point, exactly as it is for End: `record_sale` also
    // ends the listing on the server, and no patch to the one row could show
    // that on its own.
    await waitFor(() => expect(api.listListings).toHaveBeenCalledTimes(2))
    expect(
      await screen.findByText('Recorded C-0007 sold to coinfan88 for 94.65 net.'),
    ).toBeVisible()
  })

  it('shows a refusal to end without losing the table', async () => {
    const user = userEvent.setup()
    api.endListing.mockRejectedValue(new Error(STALE))
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await endOffer(user, row)

    expect(await screen.findByText(/changed by someone else/)).toBeVisible()
    expect(rowFor('eBay')).toBeInTheDocument()
    expect(api.listListings).toHaveBeenCalledTimes(1)
  })

  // In StrictMode, which is how the console really runs (`owner/main.jsx`),
  // React runs every effect setup, cleanup, setup on mount. The form's "still
  // mounted?" guard is armed in a setup and disarmed by its cleanup, so unless
  // the setup re-arms it the guard is disarmed for the dialog's whole life: a
  // save that SUCCEEDS then never closes the dialog, which sits on "Saving..."
  // forever.
  it('applies a successful save in StrictMode, where effects run twice', async () => {
    const user = userEvent.setup()
    api.updateListing.mockResolvedValue({ ...EBAY, price: '175.00', version: 4 })
    renderPage({ strict: true })
    const dialog = await openEditor(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('175.00 USD')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('does not apply a save the user cancelled before it resolved', async () => {
    const user = userEvent.setup()
    let resolveSave
    api.updateListing.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve
      }),
    )
    renderPage()
    const dialog = await openEditor(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).toBeNull()

    await act(async () => {
      resolveSave({ ...EBAY, price: '175.00', version: 4 })
    })

    expect(screen.queryByText('175.00 USD')).toBeNull()
  })
})

describe('a lot listing', () => {
  // `listing.inventory_item_id` became nullable when lots arrived, and
  // `ListingOut.item_code` with it. This page was in no task's file list, so
  // every lot row rendered a blank first cell and built the aria-label
  // "Edit null on eBay" -- on the page whose whole job is listing offers.
  beforeEach(() => {
    api.listListings.mockResolvedValue([LOT])
  })

  it('names a lot row by the lot, not by a null item code', async () => {
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    expect(within(row).getByText('Three Morgans (3 items)')).toBeInTheDocument()
    // The word itself, because that is what the broken version printed --
    // and an assertion that merely finds *something* in the cell passes
    // against a cell holding "null".
    expect(within(row).queryByText('null')).toBeNull()
  })

  it('labels the edit window for a lot without a null in it', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    expect(
      screen.getByRole('dialog', { name: 'Edit Three Morgans (3 items) on eBay' }),
    ).toBeVisible()
  })

  it('says how many coins a lot holds, so it does not read as one coin', async () => {
    // The count is the whole point of naming the lot rather than the
    // listing: "Three Morgans" at 1000.00 is a plausible single coin.
    api.listListings.mockResolvedValue([{ ...LOT, member_count: 1 }])
    renderPage()
    const row = await screen.findByRole('row', { name: /^eBay/ })
    expect(within(row).getByText('Three Morgans (1 item)')).toBeInTheDocument()
  })
})

describe('naming what a listing offers', () => {
  it('uses the item code when there is one', () => {
    expect(subjectOf(EBAY)).toBe('C-0007')
  })

  it('falls back to the title alone when a lot row has no count', () => {
    // Nothing is invented: a row with no `member_count` is named, not
    // counted. "(undefined items)" would be worse than the null it replaced.
    expect(subjectOf({ ...LOT, member_count: null })).toBe('Three Morgans')
  })
})

describe('margin', () => {
  it.each([
    ['189.00', '120.00', '36.5'],
    ['199.00', '120.00', '39.7'],
    ['100.00', '100.00', '0'],
    // Offered below cost. A negative margin is a fact worth showing.
    ['100.00', '150.00', '-50'],
    // A JSON number, in case the API ever stops sending Decimal as a string.
    [189, 120, '36.5'],
    // Unknowable, and therefore blank rather than zero.
    ['189.00', null, ''],
    ['189.00', undefined, ''],
    [null, '120.00', ''],
    ['0.00', '10.00', ''],
  ])('margin on %s costing %s is %s', (price, cost, expected) => {
    expect(marginPercent(price, cost)).toBe(expected)
  })
})
