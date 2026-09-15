import userEvent from '@testing-library/user-event'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listVendors: vi.fn(),
    createVendor: vi.fn(),
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    createPurchaseOrder: vi.fn(),
    createInventoryItem: vi.fn(),
  },
}))

import { api } from '../api'
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
    render(<NewPurchase />)

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
})

describe('NewPurchase: creating a purchase', () => {
  it('creates a new purchase for the picked vendor', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    render(<NewPurchase />)

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

  it('keeps what was typed when the order is refused as a duplicate', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockRejectedValue(
      new Error('ebay.com order PO-2 is already recorded'),
    )
    render(<NewPurchase />)

    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.type(screen.getByLabelText(/order number/i), 'PO-2')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    expect(await screen.findByText(/already recorded/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/order number/i)).toHaveValue('PO-2')
  })
})

describe('NewPurchase: picking an existing purchase', () => {
  it('opens the item form once an existing purchase is picked', async () => {
    const user = userEvent.setup()
    const detail = { ...FRESH_PURCHASE, id: 11, order_number: 'PO-1', lines: [] }
    api.getPurchaseOrder.mockResolvedValue(detail)
    render(<NewPurchase />)

    await user.click(
      screen.getByRole('radio', { name: /add to an existing purchase/i }),
    )
    await user.click(await screen.findByText(/PO-1/))

    expect(api.getPurchaseOrder).toHaveBeenCalledWith(11)
    expect(await screen.findByRole('heading', { name: /PO-1/ })).toBeInTheDocument()
  })
})

describe('NewPurchase: items on the purchase', () => {
  async function openPurchase() {
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    const user = userEvent.setup()
    render(<NewPurchase />)
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
    render(<NewPurchase />)
    const vendorSelect = await screen.findByRole('combobox', { name: 'Vendor' })
    await user.selectOptions(vendorSelect, '3')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    const row = (await screen.findByText('CC-000001')).closest('tr')
    expect(within(row).getByText('Morgan dollar')).toBeInTheDocument()
    expect(within(row).getByText('coin')).toBeInTheDocument()
    expect(within(row).getByText('25.00')).toBeInTheDocument()
    expect(within(row).getByText('ordered')).toBeInTheDocument()
  })

  it('offers a Receive these link to the picked purchase', async () => {
    await openPurchase()
    expect(screen.getByRole('link', { name: /receive these/i })).toHaveAttribute(
      'href',
      '/receiving?order=22',
    )
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
})
