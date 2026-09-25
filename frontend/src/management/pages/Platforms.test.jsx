import userEvent from '@testing-library/user-event'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listSalesVenues: vi.fn(),
    createSalesVenue: vi.fn(),
    updateSalesVenue: vi.fn(),
    listVendors: vi.fn(),
  },
}))

import { api } from '../api'
import Platforms from './Platforms'
import { fractionToPercent, percentToFraction } from './platform-rates'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

const STORE = {
  code: 'store',
  name: 'Web store',
  kind: 'own_store',
  is_own_store: true,
  vendor_id: null,
  vendor_name: null,
  account_handle: null,
  listing_url_template: null,
  commission_rate: null,
  processing_rate: null,
  processing_fixed: null,
  listing_fee: null,
  terms_as_of: null,
  notes: null,
  is_active: true,
  version: 1,
}

const EBAY = {
  ...STORE,
  code: 'ebay',
  name: 'eBay',
  kind: 'marketplace',
  is_own_store: false,
  vendor_id: 1,
  vendor_name: 'ebay.com',
  account_handle: 'you518',
  listing_url_template: 'https://www.ebay.com/itm/{external_id}',
  commission_rate: '0.1325',
  processing_fixed: '0.40',
  terms_as_of: '2026-09-17',
}

// A platform that really charges nothing: every fee is zero, not absent.
const FREE = {
  ...STORE,
  code: 'club',
  name: 'Club table',
  kind: 'marketplace',
  is_own_store: false,
  commission_rate: '0.0000',
  processing_rate: '0.0000',
  processing_fixed: '0.00',
  listing_fee: '0.00',
}

const kinds = emptyReference({
  tables: {
    sales_venue_kind: [
      { code: 'own_store', label: 'Our web store', source: 'seeded' },
      { code: 'marketplace', label: 'Marketplace', source: 'seeded' },
      { code: 'auction_house', label: 'Auction house (agent)', source: 'seeded' },
    ],
  },
})

function renderPage(options = {}) {
  return renderWithProviders(<Platforms />, {
    auth: adminAuth(),
    reference: kinds,
    ...options,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listSalesVenues.mockResolvedValue([STORE, EBAY])
  api.listVendors.mockResolvedValue([
    { id: 1, name: 'ebay.com', vendor_kind: 'marketplace' },
    { id: 2, name: 'whatnot.com', vendor_kind: 'marketplace' },
  ])
})

describe('Platforms', () => {
  it('lists platforms with fees shown as percentages', async () => {
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    expect(within(row).getByText('Marketplace')).toBeInTheDocument()
    expect(within(row).getByText('13.25% + $0.40')).toBeInTheDocument()
    expect(within(row).getByText('ebay.com')).toBeInTheDocument()
  })

  // A fee of zero is a fact -- "this platform charges nothing" -- and is not
  // the same as a fee nobody has looked up yet, which shows an empty cell.
  // The summary must therefore ask whether a fee is *present*, not whether it
  // is truthy. Both shapes are covered because only one of them is broken by
  // a truthiness test: the API serializes Decimal as a string today, and
  // "0.0000" is truthy, so that case happens to work. A JSON number 0 is
  // falsy and vanishes -- a silent default, which is the failure this
  // codebase treats as a bug wherever it appears.
  it.each([
    ['as decimal strings', FREE],
    [
      'as JSON numbers',
      {
        ...FREE,
        commission_rate: 0,
        processing_rate: 0,
        processing_fixed: 0,
        listing_fee: 0,
      },
    ],
  ])('shows fees that are deliberately zero, %s', async (_name, venue) => {
    api.listSalesVenues.mockResolvedValue([STORE, venue])
    renderPage()
    const row = await screen.findByRole('row', { name: /Club table/ })
    expect(within(row).getByText(/^0% \+ 0% \+ \$0/)).toBeVisible()
  })

  it('gives the form access keys and saves with Ctrl+S', async () => {
    const user = userEvent.setup()
    api.updateSalesVenue.mockResolvedValue({ ...EBAY, version: 2 })
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit eBay' })

    expect(within(dialog).getByLabelText('Name')).toHaveAttribute('accesskey', 'n')
    expect(within(dialog).getByLabelText('Purchase source')).toHaveAttribute(
      'accesskey',
      'p',
    )
    expect(within(dialog).getByLabelText('Commission %')).toHaveAttribute(
      'accesskey',
      'm',
    )
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveAttribute(
      'accesskey',
      'v',
    )

    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    await waitFor(() => expect(api.updateSalesVenue).toHaveBeenCalled())
  })

  // Read off the rendered dialog rather than the letter table the component
  // keeps: a table can agree with itself while a control is left without its
  // attribute, or given one twice. Both variants are checked because they
  // show different fields -- Code only when adding, Retired only when
  // editing -- so neither alone sees every letter in play at once.
  it.each([
    ['adding', null],
    ['editing', EBAY],
  ])('gives the form %s no repeated or browser-reserved letter', async (_name, v) => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    if (v === null) {
      await user.click(screen.getByRole('button', { name: 'Add platform' }))
    } else {
      await user.click(within(row).getByRole('button', { name: 'Edit' }))
    }

    const dialog = screen.getByRole('dialog')
    const letters = [...dialog.querySelectorAll('[accesskey]')].map((el) =>
      el.getAttribute('accesskey'),
    )
    expect(letters.length).toBeGreaterThan(10)
    expect(new Set(letters).size).toBe(letters.length)
    // Chrome and Edge keep D, E and F for the address bar and menus.
    expect(letters.filter((l) => 'def'.includes(l))).toEqual([])
  })

  it('adds a platform, sending the rate as a fraction', async () => {
    const user = userEvent.setup()
    api.createSalesVenue.mockResolvedValue({
      ...EBAY,
      code: 'whatnot',
      name: 'Whatnot',
    })
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })

    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog', { name: 'Add platform' })
    await user.type(within(dialog).getByLabelText('Name'), 'Whatnot')
    await user.type(within(dialog).getByLabelText('Code'), 'whatnot')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.selectOptions(within(dialog).getByLabelText('Purchase source'), '2')
    await user.type(within(dialog).getByLabelText('Commission %'), '8')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    // Exact, not objectContaining: SalesVenueCreate (backend/app/schemas.py)
    // has no is_active field and forbids extras, so a stray key here is
    // invisible to objectContaining but a 422 against the real API.
    expect(api.createSalesVenue).toHaveBeenCalledWith({
      code: 'whatnot',
      name: 'Whatnot',
      kind: 'marketplace',
      vendor_id: 2,
      account_handle: null,
      listing_url_template: null,
      commission_rate: '0.08',
      processing_rate: null,
      processing_fixed: null,
      listing_fee: null,
      terms_as_of: null,
      notes: null,
    })
  })

  // In StrictMode, which is how the console really runs (`management/main.jsx`),
  // React runs every effect setup, cleanup, setup on mount. This form's
  // "still mounted?" guard is armed in a setup and disarmed by its cleanup,
  // so unless the setup re-arms it the guard is disarmed for the dialog's
  // whole life: a save that SUCCEEDS then never reaches `onSaved`, the dialog
  // never closes and the button sits on "Saving..." forever. The guard itself
  // is load-bearing -- see "does not apply a save the user cancelled" below,
  // which is exactly what it is for -- so it is re-armed, not removed.
  it('applies a successful save in StrictMode, where effects run twice', async () => {
    const user = userEvent.setup()
    api.createSalesVenue.mockResolvedValue({
      ...EBAY,
      code: 'whatnot',
      name: 'Whatnot',
    })
    renderPage({ strict: true })
    await screen.findByRole('row', { name: /eBay/ })

    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog', { name: 'Add platform' })
    await user.type(within(dialog).getByLabelText('Name'), 'Whatnot')
    await user.type(within(dialog).getByLabelText('Code'), 'whatnot')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('row', { name: /Whatnot/ })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('does not offer the web store kind for a new platform', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const kind = within(screen.getByRole('dialog')).getByLabelText('Kind')
    expect(within(kind).queryByRole('option', { name: 'Our web store' })).toBeNull()
  })

  it('offers only unlinked purchase sources', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const source = within(screen.getByRole('dialog')).getByLabelText('Purchase source')
    expect(within(source).queryByRole('option', { name: 'ebay.com' })).toBeNull()
    expect(
      within(source).getByRole('option', { name: 'whatnot.com' }),
    ).toBeInTheDocument()
  })

  it('edits a platform, sending its version', async () => {
    const user = userEvent.setup()
    api.updateSalesVenue.mockResolvedValue({ ...EBAY, name: 'eBay US', version: 2 })
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit eBay' })
    const name = within(dialog).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'eBay US')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.updateSalesVenue).toHaveBeenCalledWith(
      'ebay',
      expect.objectContaining({
        name: 'eBay US',
        version: 1,
        commission_rate: '0.1325',
      }),
    )
    expect(await screen.findByRole('row', { name: /eBay US/ })).toBeInTheDocument()
  })

  it('does not let the web store change kind or be retired', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /Web store/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Web store' })
    expect(within(dialog).queryByLabelText('Kind')).toBeNull()
    expect(within(dialog).queryByLabelText('Retired')).toBeNull()
  })

  it('saves the web store without a kind or is_active, since the form omits both', async () => {
    const user = userEvent.setup()
    api.updateSalesVenue.mockResolvedValue(STORE)
    renderPage()
    const row = await screen.findByRole('row', { name: /Web store/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Web store' })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    // Exact: SalesVenueUpdate accepts kind and is_active, but the store's
    // form never collects either, so neither belongs in what gets sent.
    expect(api.updateSalesVenue).toHaveBeenCalledWith('store', {
      name: 'Web store',
      vendor_id: null,
      account_handle: null,
      listing_url_template: null,
      commission_rate: null,
      processing_rate: null,
      processing_fixed: null,
      listing_fee: null,
      terms_as_of: null,
      notes: null,
      version: 1,
    })
  })

  it('does not apply a save the user cancelled before it resolved', async () => {
    const user = userEvent.setup()
    let resolveCreate
    api.createSalesVenue.mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
    )
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })

    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog', { name: 'Add platform' })
    await user.type(within(dialog).getByLabelText('Name'), 'Whatnot')
    await user.type(within(dialog).getByLabelText('Code'), 'whatnot')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).toBeNull()

    await act(async () => {
      resolveCreate({ ...EBAY, code: 'whatnot', name: 'Whatnot' })
    })

    expect(screen.queryByRole('row', { name: /Whatnot/ })).toBeNull()
  })

  it('shows a sample listing link from the template', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByText('https://www.ebay.com/itm/123456789'),
    ).toBeInTheDocument()
  })

  it('shows a refusal and keeps the form open', async () => {
    const user = userEvent.setup()
    api.createSalesVenue.mockRejectedValue(
      new Error('A platform with code ebay already exists'),
    )
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'eBay')
    await user.type(within(dialog).getByLabelText('Code'), 'ebay')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await within(dialog).findByText(/already exists/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('rate conversion', () => {
  it.each([
    ['13.25', '0.1325'],
    ['8', '0.08'],
    ['0.5', '0.005'],
    ['100', '1'],
    ['2.9', '0.029'],
    ['', null],
  ])('percent %s is fraction %s', (percent, fraction) => {
    expect(percentToFraction(percent)).toBe(fraction)
  })

  it.each([
    ['0.1325', '13.25'],
    ['0.0800', '8'],
    ['0.0050', '0.5'],
    ['1.0000', '100'],
    [null, ''],
  ])('fraction %s is percent %s', (fraction, percent) => {
    expect(fractionToPercent(fraction)).toBe(percent)
  })
})
