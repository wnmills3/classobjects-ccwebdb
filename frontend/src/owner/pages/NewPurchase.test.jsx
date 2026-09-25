import userEvent from '@testing-library/user-event'
import { act, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listVendors: vi.fn(),
    createVendor: vi.fn(),
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    createPurchaseOrder: vi.fn(),
    updatePurchaseOrder: vi.fn(),
    createInventoryItem: vi.fn(),
    suggestNote: vi.fn(() => Promise.resolve({})),
    suggestCoin: vi.fn(() => Promise.resolve({})),
  },
}))

import { api } from '../api'
import { renderWithProviders } from '../../test/helpers'
import NewPurchase from './NewPurchase'

const VENDORS = [
  { id: 3, name: 'ebay.com', url: 'https://www.ebay.com', vendor_kind: 'marketplace' },
]

const EXISTING_ORDERS = [
  {
    id: 11,
    order_number: 'PO-1',
    vendor: 'Heritage',
    ordered_on: '2026-01-05',
    outstanding: 1,
    total: 2,
  },
]

const FRESH_PURCHASE = {
  id: 22,
  order_number: 'PO-2',
  vendor: 'ebay.com',
  ordered_on: '2026-02-01',
  source_url: null,
  lines: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listVendors.mockResolvedValue(VENDORS)
  api.listPurchaseOrders.mockResolvedValue(EXISTING_ORDERS)
})

describe('NewPurchase: creating a vendor inline', () => {
  it('adds a vendor and selects it', async () => {
    const user = userEvent.setup()
    api.createVendor.mockResolvedValue({
      id: 9,
      name: 'New Vendor',
      url: null,
      vendor_kind: 'unknown',
    })
    renderWithProviders(<NewPurchase />)

    await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )

    await user.type(screen.getByPlaceholderText('Vendor name'), 'New Vendor')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() =>
      expect(api.createVendor).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'New Vendor' }),
      ),
    )
    expect(await screen.findByRole('combobox', { name: 'Vendor' })).toHaveValue('9')
  })

  it('does not submit the outer purchase form when Enter is pressed in the vendor name field', async () => {
    const user = userEvent.setup()
    api.createVendor.mockResolvedValue({
      id: 9,
      name: 'New Vendor',
      url: null,
      vendor_kind: 'unknown',
    })
    renderWithProviders(<NewPurchase />)

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )
    await user.type(screen.getByPlaceholderText('Vendor name'), 'New Vendor{Enter}')

    await waitFor(() => expect(api.createVendor).toHaveBeenCalled())
    expect(api.createPurchaseOrder).not.toHaveBeenCalled()
  })

  it('cancels the vendor draft when Enter activates Cancel, without creating it', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )
    await user.type(screen.getByPlaceholderText('Vendor name'), 'Abandoned')
    // Tab to Cancel and press Enter, as a keyboard user would.
    screen.getByRole('button', { name: 'Cancel' }).focus()
    await user.keyboard('{Enter}')

    expect(api.createVendor).not.toHaveBeenCalled()
    expect(await screen.findByRole('combobox', { name: 'Vendor' })).toBeInTheDocument()
  })
})

describe('NewPurchase: creating a purchase', () => {
  it('creates a new purchase for the picked vendor', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    renderWithProviders(<NewPurchase />)

    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.type(screen.getByLabelText(/order number/i), 'PO-2')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    await waitFor(() =>
      expect(api.createPurchaseOrder).toHaveBeenCalledWith(
        expect.objectContaining({ vendor_id: 3, order_number: 'PO-2' }),
      ),
    )
    expect(await screen.findByText(/PO-2/)).toBeInTheDocument()
    expect(screen.getByText(/no items entered yet/i)).toBeInTheDocument()
  })

  it('explains each purchase and item field when it has focus', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    renderWithProviders(<NewPurchase />)

    await user.click(screen.getByLabelText(/order date/i))
    expect(screen.getByText(/not the date it arrived/)).toBeInTheDocument()

    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))
    await screen.findByText(/no items entered yet/i)

    // The New item form inside the page shares the page's help area.
    await user.click(screen.getByRole('textbox', { name: /title/i }))
    expect(screen.getByText(/kept as the seller's words/)).toBeInTheDocument()
    await user.click(screen.getByLabelText(/tax rate/i))
    expect(screen.getByText(/Every item entered below is stamped/)).toBeInTheDocument()
  })

  it('explains the existing-or-new choice when a radio is chosen', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )
    expect(
      screen.getByText(/Every item belongs to exactly one purchase/),
    ).toBeInTheDocument()
  })

  it('keeps what was typed when the order is refused as a duplicate', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockRejectedValue(
      new Error('ebay.com order PO-2 is already recorded'),
    )
    renderWithProviders(<NewPurchase />)

    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.type(screen.getByLabelText(/order number/i), 'PO-2')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    expect(await screen.findByText(/already recorded/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/order number/i)).toHaveValue('PO-2')
  })
})

describe('NewPurchase: changing a purchase after it is made', () => {
  const PURCHASE = {
    ...FRESH_PURCHASE,
    order_number: 'Order-0001',
    notes: 'from the show',
    source_text: 'Gift',
  }

  it('opens the purchase an item links to', async () => {
    api.getPurchaseOrder.mockResolvedValue(PURCHASE)
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    expect(await screen.findByText(/Order-0001/)).toBeInTheDocument()
    expect(api.getPurchaseOrder).toHaveBeenCalledWith(22)
  })

  it('sends only the details changed, and shows what the server kept', async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(PURCHASE)
    api.updatePurchaseOrder.mockResolvedValue({ ...PURCHASE, order_number: 'SD-77' })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })

    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    const number = screen.getByRole('textbox', { name: /order number/i })
    expect(number).toHaveValue('Order-0001')
    // The web address box holds the stored text, link or not.
    expect(screen.getByRole('textbox', { name: /web address/i })).toHaveValue('Gift')
    await user.clear(number)
    await user.type(number, 'SD-77')
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    await waitFor(() =>
      expect(api.updatePurchaseOrder).toHaveBeenCalledWith(22, {
        order_number: 'SD-77',
      }),
    )
    expect(await screen.findByText(/SD-77/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save details' })).toBeNull()
  })

  it('keeps the form open, as typed, when the number is refused', async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(PURCHASE)
    api.updatePurchaseOrder.mockRejectedValue(
      new Error('ebay.com order PO-1 is already recorded'),
    )
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })

    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    const number = screen.getByRole('textbox', { name: /order number/i })
    await user.clear(number)
    await user.type(number, 'PO-1')
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    expect(await screen.findByText(/PO-1 is already recorded/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /order number/i })).toHaveValue('PO-1')
  })
})

describe('NewPurchase: picking an existing purchase', () => {
  it('opens the item form once an existing purchase is picked', async () => {
    const user = userEvent.setup()
    const detail = { ...FRESH_PURCHASE, id: 11, order_number: 'PO-1', lines: [] }
    api.getPurchaseOrder.mockResolvedValue(detail)
    renderWithProviders(<NewPurchase />)

    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )
    await user.click(await screen.findByText(/PO-1/))

    expect(api.getPurchaseOrder).toHaveBeenCalledWith(11)
    expect(await screen.findByRole('heading', { name: /PO-1/ })).toBeInTheDocument()
  })

  it('renders each row as a button, so it is reachable by keyboard', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)

    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )

    expect(await screen.findByRole('button', { name: /PO-1/ })).toBeInTheDocument()
  })

  it('ignores a stale response from an earlier pick', async () => {
    const user = userEvent.setup()
    api.listPurchaseOrders.mockResolvedValue([
      ...EXISTING_ORDERS,
      {
        id: 12,
        order_number: 'PO-2',
        vendor: 'Other Vendor',
        ordered_on: '2026-01-06',
        outstanding: 0,
        total: 1,
      },
    ])
    let resolveFirst
    api.getPurchaseOrder.mockImplementation((id) => {
      if (id === 11) {
        return new Promise((resolve) => {
          resolveFirst = () =>
            resolve({ ...FRESH_PURCHASE, id: 11, order_number: 'PO-1' })
        })
      }
      return Promise.resolve({ ...FRESH_PURCHASE, id: 12, order_number: 'PO-2' })
    })
    renderWithProviders(<NewPurchase />)

    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )
    // The slower pick (PO-1) is made first, the faster one (PO-2) second --
    // PO-2's response lands first, and PO-1's must not overwrite it when it
    // finally arrives.
    await user.click(await screen.findByRole('button', { name: /PO-1/ }))
    await user.click(screen.getByRole('button', { name: /PO-2/ }))
    await screen.findByRole('heading', { name: /PO-2/ })

    // `act` is what makes this test able to fail. A bare `resolveFirst()`
    // followed by `waitFor` passes even with the guard deleted: waitFor runs
    // its callback synchronously first, before PO-1's `.then` microtask has
    // flushed, so it only ever sees the state from before the late response.
    await act(async () => {
      resolveFirst()
    })
    expect(screen.getByRole('heading', { name: /PO-2/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /PO-1/ })).not.toBeInTheDocument()
  })

  it('filters the list by order number or vendor, case-insensitively', async () => {
    const user = userEvent.setup()
    api.listPurchaseOrders.mockResolvedValue([
      ...EXISTING_ORDERS,
      {
        id: 12,
        order_number: 'PO-2',
        vendor: 'Other Vendor',
        ordered_on: '2026-01-06',
        outstanding: 0,
        total: 1,
      },
    ])
    renderWithProviders(<NewPurchase />)

    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )
    await screen.findByRole('button', { name: /PO-1/ })

    await user.type(screen.getByRole('textbox', { name: /filter/i }), 'heritage')

    expect(screen.getByRole('button', { name: /PO-1/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /PO-2/ })).not.toBeInTheDocument()
  })
})

describe('NewPurchase: items on the purchase', () => {
  async function openPurchase(options = {}) {
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />, options)
    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))
    await screen.findByText(/no items entered yet/i)
    return user
  }

  it('lists item code, title, kind, cost and status from the purchase lines', async () => {
    api.createPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      lines: [
        {
          id: 1,
          item_code: 'CC-000001',
          source_title: 'Morgan dollar',
          item_kind: 'coin',
          item_cost: '25.00',
          status: 'ordered',
        },
      ],
    })
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    const row = (await screen.findByText('CC-000001')).closest('tr')
    expect(within(row).getByText('Morgan dollar')).toBeInTheDocument()
    expect(within(row).getByText('coin')).toBeInTheDocument()
    expect(within(row).getByText('25.00')).toBeInTheDocument()
    expect(within(row).getByText('ordered')).toBeInTheDocument()
  })

  it('offers a Receive these link built from the router, not a hard-coded shop path', async () => {
    // The console mounts at basename "/owner" (owner/main.jsx); a `<Link>`
    // folds that into the rendered href, while a hard-coded
    // `<a href="/receiving?...">` would not and would send the browser to
    // the shop at the site root instead of Receiving.
    await openPurchase({ basename: '/owner', route: '/owner/purchases/new' })
    const link = screen.getByRole('link', { name: /receive these/i })
    expect(link).toHaveAttribute('href', '/owner/receiving?order=22')
  })

  it('passes the resolved tax defaults down to the New item form', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 100 })
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), '0.05')
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ purchase_order_id: 22, tax_rate: '0.05' }),
      ),
    )
  })

  it('sends a zero rate once "No sales tax charged" is ticked', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 101 })
    const user = await openPurchase()

    await user.click(screen.getByRole('checkbox', { name: /no sales tax charged/i }))
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ tax_rate: '0' }),
      ),
    )
  })

  it('sends a null rate -- the configured default -- when the field is left blank', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 102 })
    const user = await openPurchase()

    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ tax_rate: null }),
      ),
    )
  })

  it('accepts a leading-dot tax rate, as the server does', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 103 })
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), '.0635')
    expect(screen.queryByText(/enter a rate between/i)).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ tax_rate: '.0635' }),
      ),
    )
  })

  it('disables saving items, with a visible reason, while the tax rate is invalid', async () => {
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), 'abc')

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(
      screen.getByText(/fix the tax rate above before saving items/i),
    ).toBeInTheDocument()
    expect(api.createInventoryItem).not.toHaveBeenCalled()
  })

  it('unblocks saving when "No sales tax charged" settles an invalid rate', async () => {
    // Ticking the box disables the rate box. A refusal that points at a field
    // the user can no longer edit is a dead end: the only way out would be to
    // untick, fix the rate, and tick again.
    api.createInventoryItem.mockResolvedValue({ id: 104 })
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), 'abc')
    await user.click(screen.getByRole('checkbox', { name: /no sales tax charged/i }))

    expect(
      screen.queryByText(/fix the tax rate above before saving items/i),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/enter a rate between/i)).not.toBeInTheDocument()

    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ tax_rate: '0' }),
      ),
    )
  })

  describe('Tax on shipping', () => {
    it('sends null ("As configured") by default', async () => {
      api.createInventoryItem.mockResolvedValue({ id: 200 })
      const user = await openPurchase()

      await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      await waitFor(() =>
        expect(api.createInventoryItem).toHaveBeenCalledWith(
          expect.objectContaining({ tax_includes_shipping: null }),
        ),
      )
    })

    it('sends false once "Not taxed" is chosen', async () => {
      api.createInventoryItem.mockResolvedValue({ id: 201 })
      const user = await openPurchase()

      await user.selectOptions(
        screen.getByRole('combobox', { name: /tax on shipping/i }),
        'Not taxed',
      )
      await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      await waitFor(() =>
        expect(api.createInventoryItem).toHaveBeenCalledWith(
          expect.objectContaining({ tax_includes_shipping: false }),
        ),
      )
    })

    it('sends true once "Taxed" is chosen', async () => {
      api.createInventoryItem.mockResolvedValue({ id: 202 })
      const user = await openPurchase()

      await user.selectOptions(
        screen.getByRole('combobox', { name: /tax on shipping/i }),
        'Taxed',
      )
      await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      await waitFor(() =>
        expect(api.createInventoryItem).toHaveBeenCalledWith(
          expect.objectContaining({ tax_includes_shipping: true }),
        ),
      )
    })
  })

  it('shows the error rather than swallowing it when reloading the purchase fails', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 400 })
    const user = await openPurchase()
    api.getPurchaseOrder.mockRejectedValueOnce(new Error('lost connection'))

    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/lost connection/i)).toBeInTheDocument()
  })

  it('offers a Start another purchase button that returns to step 1', async () => {
    const user = await openPurchase()

    await user.click(screen.getByRole('button', { name: /start another purchase/i }))

    expect(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /PO-2/ })).not.toBeInTheDocument()
  })
})
