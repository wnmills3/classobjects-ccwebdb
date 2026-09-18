import userEvent from '@testing-library/user-event'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listListings: vi.fn(),
    updateListing: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
  },
}))

import { api } from '../api'
import Listings from './Listings'
import { marginPercent } from './platform-rates'
import { date } from '../../shared/format'
import { adminAuth, renderWithProviders } from '../../test/helpers'

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

const VENUES = [
  { code: 'store', name: 'Web store', is_own_store: true, is_active: true },
  { code: 'ebay', name: 'eBay', is_own_store: false, is_active: true },
]

const STALE = 'This offer was changed by someone else. Reload and reapply your changes.'

/** The row for one platform. Anchored: "eBay" also appears inside the paused
 * row's explanation ("paused for listing #14 on eBay"), and the platform is
 * the first cell, so only an anchored match picks out the right row. */
const rowFor = (platform) =>
  screen.getByRole('row', { name: new RegExp(`^${platform}`) })

function renderPage(options = {}) {
  return renderWithProviders(<Listings />, { auth: adminAuth(), ...options })
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
