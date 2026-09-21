import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    recordSale: vi.fn(),
  },
}))

import { api } from '../api'
import { ApiError } from '../../shared/api'
import RecordSaleDialog from './RecordSaleDialog'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

// An active eBay listing. Its cost basis (120.00) is a fourth number,
// distinct from every price and fee used below, so a margin assertion can
// only be satisfied by the real subtraction.
const LISTING = {
  id: 14,
  item_id: 7,
  item_code: 'C-0007',
  item_title: '1881-S Morgan Dollar',
  venue: 'ebay',
  venue_name: 'eBay',
  format: 'fixed_price',
  status: 'active',
  price: '199.00',
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

const HERITAGE = { ...LISTING, id: 22, venue: 'heritage', venue_name: 'Heritage' }

const feeKinds = emptyReference({
  tables: {
    sales_fee_kind: [
      { code: 'commission', label: 'Commission', is_active: true },
      { code: 'processing', label: 'Payment processing', is_active: true },
      { code: 'listing', label: 'Listing fee', is_active: true },
      { code: 'shipping_label', label: 'Shipping label', is_active: true },
      { code: 'promotion', label: 'Promotion', is_active: true },
      { code: 'other', label: 'Other', is_active: true },
    ],
  },
})

function renderDialog(props = {}, options = {}) {
  return renderWithProviders(
    <RecordSaleDialog
      listing={LISTING}
      isAuctionHouse={false}
      onRecorded={vi.fn()}
      onClose={vi.fn()}
      {...props}
    />,
    { auth: adminAuth(), reference: feeKinds, ...options },
  )
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('RecordSaleDialog', () => {
  // Gross (115.00), the one fee (20.35) and net (94.65) are three different
  // numbers, matching the backend's own fixture -- a price of 100 with a fee
  // of 50 leaving a net of 50 cannot tell fee total from net apart.
  it('sends the sale price and only the fees the user entered, as strings', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.type(screen.getByLabelText('Commission'), '20.35')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    expect(api.recordSale).toHaveBeenCalledWith(14, {
      price: '115.00',
      buyer_username: null,
      external_order_id: null,
      fees: [{ kind: 'commission', amount: '20.35' }],
      equal_shares: false,
    })
  })

  it('sends the buyer and order number when they are typed', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.type(screen.getByLabelText('Buyer username'), 'coinfan88')
    await user.type(screen.getByLabelText('Order number'), '04-12345-67890')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    expect(api.recordSale).toHaveBeenCalledWith(
      14,
      expect.objectContaining({
        buyer_username: 'coinfan88',
        external_order_id: '04-12345-67890',
      }),
    )
  })

  it('shows a live gross/fees/net/margin summary before confirming', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.type(screen.getByLabelText('Commission'), '20.35')

    expect(screen.getByText('115.00')).toBeInTheDocument()
    expect(screen.getByText('20.35')).toBeInTheDocument()
    expect(screen.getByText('94.65')).toBeInTheDocument()
    // cost 120.00, net 94.65, gross 115.00: ((94.65-120.00)/115.00)*100 = -22.0
    expect(screen.getByText('-22%')).toBeInTheDocument()
  })

  it('says plainly that a blank buyer means the undisclosed buyer', async () => {
    renderDialog({ isAuctionHouse: false })
    expect(
      screen.getByText(/Leave the buyer blank if the platform does not name/),
    ).toBeVisible()
  })

  it('names the auction house when a blank buyer is its undisclosed one', async () => {
    renderDialog({ listing: HERITAGE, isAuctionHouse: true })
    expect(
      screen.getByText(/Leave the buyer blank for Heritage's undisclosed buyer/),
    ).toBeVisible()
  })

  it('refuses a fee amount that is not money, without calling the API', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.type(screen.getByLabelText('Commission'), 'lots')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    expect(api.recordSale).not.toHaveBeenCalled()
    expect(
      screen.getByText(/Commission fee must be an amount like 20.35/),
    ).toBeVisible()
  })

  // A 409 -- `SaleRefused` -- always carries `detail` as a plain string.
  it('lists a plain-string refusal instead of closing', async () => {
    const user = userEvent.setup()
    const onRecorded = vi.fn()
    api.recordSale.mockRejectedValue(
      new ApiError(409, 'Listing 14 is not on offer (ended)', {
        detail: 'Listing 14 is not on offer (ended)',
      }),
    )
    renderDialog({ onRecorded })
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    expect(await screen.findByText('Listing 14 is not on offer (ended)')).toBeVisible()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onRecorded).not.toHaveBeenCalled()
  })

  // A Pydantic validation 422 carries `detail` as a LIST of error objects,
  // not a string. `ApiError.message` is always the already-flattened
  // string either way (`shared/api.js`'s `readDetail`); a handler that
  // reads `err.body.detail` or `err.detail` directly instead of
  // `err.message` would get the raw array here and either render
  // "[object Object]" or throw trying to render it as a child.
  it('shows a readable message for a structured 422, not [object Object]', async () => {
    const user = userEvent.setup()
    const onRecorded = vi.fn()
    api.recordSale.mockRejectedValue(
      new ApiError(422, 'fees.0.amount: Input should be greater than or equal to 0', {
        detail: [
          {
            loc: ['body', 'fees', 0, 'amount'],
            msg: 'Input should be greater than or equal to 0',
            type: 'greater_than_equal',
          },
        ],
      }),
    )
    renderDialog({ onRecorded })
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    expect(
      await screen.findByText(
        'fees.0.amount: Input should be greater than or equal to 0',
      ),
    ).toBeVisible()
    expect(screen.queryByText('[object Object]')).toBeNull()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onRecorded).not.toHaveBeenCalled()
  })

  // In StrictMode, which is how the console really runs (`owner/main.jsx`),
  // React runs every effect setup, cleanup, setup on mount. The "still
  // mounted?" guard is armed in a setup and disarmed by its cleanup, so
  // unless the setup re-arms it, the guard is disarmed for the dialog's
  // whole life: a sale the server accepted would never reach `onRecorded`.
  it('reaches onRecorded after a successful save in StrictMode', async () => {
    const user = userEvent.setup()
    const onRecorded = vi.fn()
    api.recordSale.mockResolvedValue({
      id: 1,
      external_order_id: null,
      total_amount: '115.00',
      fee_total: '20.35',
      net_amount: '94.65',
      buyer: 'Undisclosed buyer (eBay)',
      item_codes: ['C-0007'],
    })
    renderDialog({ onRecorded }, { strict: true })
    await user.type(screen.getByLabelText('Sale price'), '115.00')
    await user.click(screen.getByRole('button', { name: 'Record sale' }))

    await waitFor(() => expect(onRecorded).toHaveBeenCalledTimes(1))
  })

  it('gives the dialog unique, unreserved access keys', async () => {
    renderDialog()
    const dialog = screen.getByRole('dialog')
    const letters = [...dialog.querySelectorAll('[accesskey]')].map((el) =>
      el.getAttribute('accesskey'),
    )
    expect(letters.length).toBeGreaterThan(0)
    expect(new Set(letters).size).toBe(letters.length)
    expect(letters.filter((l) => 'def'.includes(l))).toEqual([])
  })
})
