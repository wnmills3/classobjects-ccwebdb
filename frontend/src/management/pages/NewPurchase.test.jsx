import userEvent from '@testing-library/user-event'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listVendors: vi.fn(),
    createVendor: vi.fn(),
    listSellers: vi.fn(),
    createSeller: vi.fn(),
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    createPurchaseOrder: vi.fn(),
    updatePurchaseOrder: vi.fn(),
    createInventoryItem: vi.fn(),
    getInventoryItem: vi.fn(),
    deleteInventoryItem: vi.fn(),
  },
}))

// The editor itself is tested on its own; here it stands in for the dialog
// the purchase page opens, and says which item it was given.
vi.mock('./inventory/ItemEditDialog', () => ({
  default: ({ itemId, onSaved, onClose }) => (
    <div role="dialog" aria-label="Edit item">
      editing item {itemId}
      <button onClick={onSaved}>stub save</button>
      <button onClick={onClose}>stub close</button>
    </div>
  ),
}))

import { api } from '../api'
import { renderWithProviders } from '../../test/helpers'
import NewPurchase from './NewPurchase'

const VENDORS = [
  { id: 3, name: 'ebay.com', url: 'https://www.ebay.com', vendor_kind: 'marketplace' },
]

const SELLERS = [
  { id: 5, name: 'coind0g', store_url: 'https://www.ebay.com/usr/coind0g' },
  { id: 6, name: 'deswin3834', store_url: null },
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
  api.listSellers.mockResolvedValue(SELLERS)
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

  it('asks for the web address first and proposes the name from its last part', async () => {
    const user = userEvent.setup()
    api.createVendor.mockResolvedValue({
      id: 9,
      name: 'goldstandardauctions.hibid.com',
      url: 'https://goldstandardauctions.hibid.com/',
      vendor_kind: null,
    })
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )
    const address = screen.getByRole('textbox', { name: 'Vendor web address' })
    const name = screen.getByPlaceholderText('Vendor name')
    // The address comes before the name, and has the cursor.
    expect(address.compareDocumentPosition(name)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(address).toHaveFocus()

    // Not an address yet: nothing to propose.
    await user.type(address, 'https://www')
    expect(name).toHaveValue('')
    await user.type(address, '.ebay.com')
    // Only a site so far: the site, without www.
    expect(name).toHaveValue('ebay.com')
    await user.type(address, '/usr/drh9989')
    // A page on it: the page's last part, the account on the marketplace.
    expect(name).toHaveValue('drh9989')

    // What follows ? is no part of a name, and the case is the address's.
    await user.clear(address)
    expect(name).toHaveValue('')
    await user.type(
      address,
      'https://www.etsy.com/shop/TheCoinTraderOnline?ref=view_receipt',
    )
    expect(name).toHaveValue('TheCoinTraderOnline')

    // A site of the vendor's own under the marketplace's: its first part,
    // whatever page follows.
    await user.clear(address)
    await user.type(address, 'https://goldstandardauctions.hibid.com/lots')
    expect(name).toHaveValue('goldstandardauctions')

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createVendor).toHaveBeenCalledWith({
        name: 'goldstandardauctions',
        vendor_kind: null,
        url: 'https://goldstandardauctions.hibid.com/lots',
      }),
    )
  })

  it('names a plain site for the site', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )

    await user.type(
      screen.getByRole('textbox', { name: 'Vendor web address' }),
      'https://usmint.gov/',
    )

    expect(screen.getByPlaceholderText('Vendor name')).toHaveValue('usmint.gov')
  })

  it('proposes a name from an address typed without https://', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )

    await user.type(
      screen.getByRole('textbox', { name: 'Vendor web address' }),
      'WWW.APMEX.com',
    )

    expect(screen.getByPlaceholderText('Vendor name')).toHaveValue('apmex.com')
  })

  it('keeps a name typed by hand when the address changes', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '__add__',
    )
    const name = screen.getByPlaceholderText('Vendor name')

    await user.type(name, 'Local coin shop')
    await user.type(
      screen.getByRole('textbox', { name: 'Vendor web address' }),
      'https://example.com',
    )

    expect(name).toHaveValue('Local coin shop')
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

    // The New item row inside the page shares the page's help area.
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
    expect(screen.getByRole('textbox', { name: /^web address/i })).toHaveValue('Gift')
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

  it('gives a purchase with no number one on save, box left blank', async () => {
    const user = userEvent.setup()
    const unnumbered = { ...PURCHASE, order_number: null }
    api.getPurchaseOrder.mockResolvedValue(unnumbered)
    api.updatePurchaseOrder.mockResolvedValue({
      ...unnumbered,
      order_number: 'Order-0001',
    })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })

    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    await waitFor(() =>
      expect(api.updatePurchaseOrder).toHaveBeenCalledWith(22, { order_number: null }),
    )
    // The heading, not the help band, which names the pattern too.
    expect(await screen.findByRole('heading', { level: 2 })).toHaveTextContent(
      'Order-0001',
    )
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

  it.each(['12', '#12', ' #12 '])(
    'finds a purchase by its own number, typed as "%s"',
    async (typed) => {
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

      await user.type(screen.getByRole('textbox', { name: /filter/i }), typed)

      expect(screen.getByRole('button', { name: /PO-2/ })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /PO-1/ })).not.toBeInTheDocument()
    },
  )

  it('matches a purchase number exactly, not as part of a longer one', async () => {
    const user = userEvent.setup()
    api.listPurchaseOrders.mockResolvedValue([
      ...EXISTING_ORDERS,
      {
        id: 112,
        order_number: 'PO-3',
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

    await user.type(screen.getByRole('textbox', { name: /filter/i }), '#11')

    expect(screen.getByRole('button', { name: /PO-1/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /PO-3/ })).not.toBeInTheDocument()
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
    // The console mounts at basename "/management" (management/main.jsx); a `<Link>`
    // folds that into the rendered href, while a hard-coded
    // `<a href="/receiving?...">` would not and would send the browser to
    // the shop at the site root instead of Receiving.
    await openPurchase({ basename: '/management', route: '/management/purchases/new' })
    const link = screen.getByRole('link', { name: /receive these/i })
    expect(link).toHaveAttribute('href', '/management/receiving?order=22')
  })

  it('sends the resolved tax defaults with a new item', async () => {
    api.createInventoryItem.mockResolvedValue({ id: 100 })
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), '0.05')
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A note')
    await user.click(screen.getByRole('button', { name: 'Add item' }))

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
    await user.click(screen.getByRole('button', { name: 'Add item' }))

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
    await user.click(screen.getByRole('button', { name: 'Add item' }))

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
    await user.click(screen.getByRole('button', { name: 'Add item' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ tax_rate: '.0635' }),
      ),
    )
  })

  it('disables saving items, with a visible reason, while the tax rate is invalid', async () => {
    const user = await openPurchase()

    await user.type(screen.getByLabelText(/^tax rate/i), 'abc')

    expect(screen.getByRole('button', { name: 'Add item' })).toBeDisabled()
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
    await user.click(screen.getByRole('button', { name: 'Add item' }))

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
      await user.click(screen.getByRole('button', { name: 'Add item' }))

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
      await user.click(screen.getByRole('button', { name: 'Add item' }))

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
      await user.click(screen.getByRole('button', { name: 'Add item' }))

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
    await user.click(screen.getByRole('button', { name: 'Add item' }))

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

describe('NewPurchase: the purchases table', () => {
  const ORDERS = [
    {
      id: 1,
      order_number: 'B-10',
      vendor: 'hibid.com',
      ordered_on: '2026-03-01',
      outstanding: 0,
      total: 1,
    },
    {
      id: 2,
      order_number: 'B-9',
      vendor: 'eBay.com',
      ordered_on: '2026-09-06',
      outstanding: 0,
      total: 1,
    },
    {
      id: 3,
      order_number: null,
      vendor: 'Coin show',
      ordered_on: null,
      outstanding: 0,
      total: 1,
    },
    {
      id: 4,
      order_number: 'A-1',
      vendor: 'Apmex',
      ordered_on: '2025-12-24',
      outstanding: 0,
      total: 1,
    },
  ]

  async function openTable(user) {
    api.listPurchaseOrders.mockResolvedValue(ORDERS)
    renderWithProviders(<NewPurchase />)
    await user.click(
      await screen.findByRole('radio', { name: /add to an existing purchase/i }),
    )
    return screen.findByRole('table', { name: /purchases/i })
  }

  //: Each body row's order number, as the first cell shows it.
  function numbers(table) {
    return within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0].textContent)
  }

  function header(table, name) {
    return within(table).getByRole('columnheader', { name: new RegExp(name, 'i') })
  }

  it('shows order number, date and vendor in their own columns', async () => {
    const table = await openTable(userEvent.setup())
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((th) => th.textContent.replace(/[^A-Za-z ]/g, '').trim()),
    ).toEqual(['Order number', 'Date', 'Vendor', 'No'])
    const row = within(table).getAllByRole('row')[1]
    expect(
      within(row)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['B-9', 'Sep 6, 2026', 'eBay.com', '#2'])
  })

  it('sorts by purchase number, highest first, and back', async () => {
    const user = userEvent.setup()
    const table = await openTable(user)
    await user.click(within(header(table, 'No')).getByRole('button'))
    expect(numbers(table)).toEqual(['A-1', 'no order number', 'B-9', 'B-10'])
    await user.click(within(header(table, 'No')).getByRole('button'))
    expect(numbers(table)).toEqual(['B-10', 'B-9', 'no order number', 'A-1'])
  })

  it('lists the newest purchase first, an undated one last', async () => {
    const table = await openTable(userEvent.setup())
    expect(header(table, 'date')).toHaveAttribute('aria-sort', 'descending')
    expect(numbers(table)).toEqual(['B-9', 'B-10', 'A-1', 'no order number'])
  })

  it('turns the date order around, an undated purchase still last', async () => {
    const user = userEvent.setup()
    const table = await openTable(user)
    await user.click(within(header(table, 'date')).getByRole('button'))
    expect(header(table, 'date')).toHaveAttribute('aria-sort', 'ascending')
    expect(numbers(table)).toEqual(['A-1', 'B-10', 'B-9', 'no order number'])
  })

  it('sorts by vendor A to Z, ignoring case, and back', async () => {
    const user = userEvent.setup()
    const table = await openTable(user)
    await user.click(within(header(table, 'vendor')).getByRole('button'))
    expect(header(table, 'vendor')).toHaveAttribute('aria-sort', 'ascending')
    expect(header(table, 'date')).toHaveAttribute('aria-sort', 'none')
    expect(numbers(table)).toEqual(['A-1', 'no order number', 'B-9', 'B-10'])

    await user.click(within(header(table, 'vendor')).getByRole('button'))
    expect(numbers(table)).toEqual(['B-10', 'B-9', 'no order number', 'A-1'])
  })

  it('sorts order numbers as people read them, B-9 before B-10', async () => {
    const user = userEvent.setup()
    const table = await openTable(user)
    await user.click(within(header(table, 'order number')).getByRole('button'))
    expect(numbers(table)).toEqual(['A-1', 'B-9', 'B-10', 'no order number'])
  })

  it("lists one vendor's purchases newest first", async () => {
    const user = userEvent.setup()
    api.listPurchaseOrders.mockResolvedValue([
      {
        id: 7,
        order_number: 'W-old',
        vendor: 'whatnot.com',
        ordered_on: '2026-01-02',
        outstanding: 0,
        total: 1,
      },
      {
        id: 5,
        order_number: 'W-new',
        vendor: 'whatnot.com',
        ordered_on: '2026-08-30',
        outstanding: 0,
        total: 1,
      },
    ])
    renderWithProviders(<NewPurchase />)
    await user.click(
      await screen.findByRole('radio', { name: /add to an existing purchase/i }),
    )
    const table = await screen.findByRole('table', { name: /purchases/i })
    await user.click(within(header(table, 'vendor')).getByRole('button'))

    expect(numbers(table)).toEqual(['W-new', 'W-old'])
  })

  it('picks a purchase from its row by keyboard', async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      id: 2,
      order_number: 'B-9',
    })
    const table = await openTable(user)
    within(table).getByRole('button', { name: 'B-9' }).focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(2))
  })
})

describe('NewPurchase: Ctrl+S', () => {
  const PURCHASE_22 = {
    id: 22,
    order_number: 'Order-0001',
    vendor: 'ebay.com',
    vendor_id: 3,
    ordered_on: '2026-02-01',
    source_url: null,
    notes: null,
    lines: [],
  }

  it("saves the purchase's details while they are being edited", async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(PURCHASE_22)
    api.updatePurchaseOrder.mockResolvedValue({ ...PURCHASE_22, order_number: 'SD-77' })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    const number = screen.getByRole('textbox', { name: /order number/i })
    await user.clear(number)
    await user.type(number, 'SD-77')

    fireEvent.keyDown(document, { key: 's', ctrlKey: true })

    await waitFor(() =>
      expect(api.updatePurchaseOrder).toHaveBeenCalledWith(22, {
        order_number: 'SD-77',
      }),
    )
    expect(api.createInventoryItem).not.toHaveBeenCalled()
  })

  it('adds the new item on Enter once the details editor is closed again', async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(PURCHASE_22)
    api.createInventoryItem.mockResolvedValue({ id: 30, item_code: 'CC-000030' })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'A dime{Enter}')

    await waitFor(() => expect(api.createInventoryItem).toHaveBeenCalledTimes(1))
    expect(api.updatePurchaseOrder).not.toHaveBeenCalled()
  })
})

describe('NewPurchase: the seller', () => {
  const STORE = 'https://www.ebay.com/usr/coind0g'

  it('is picked for a new purchase and linked in its heading', async () => {
    const user = userEvent.setup()
    api.createPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      seller_id: 5,
      seller: 'coind0g',
      seller_url: STORE,
    })
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '3',
    )
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Seller' }),
      '5',
    )
    await user.click(screen.getByRole('button', { name: /create purchase/i }))

    await waitFor(() =>
      expect(api.createPurchaseOrder).toHaveBeenCalledWith(
        expect.objectContaining({ seller_id: 5 }),
      ),
    )
    const link = await screen.findByRole('link', { name: 'coind0g' })
    expect(link).toHaveAttribute('href', STORE)
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('is added inline with a name and store, and chosen', async () => {
    const user = userEvent.setup()
    api.createSeller.mockResolvedValue({ id: 9, name: 'newseller', store_url: STORE })
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Vendor' }),
      '3',
    )
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Seller' }),
      '__add__',
    )
    await user.type(screen.getByPlaceholderText('Seller name'), ' newseller ')
    await user.type(screen.getByPlaceholderText('Store web address or email'), STORE)
    await user.click(screen.getByRole('button', { name: 'Add seller' }))

    await waitFor(() =>
      expect(api.createSeller).toHaveBeenCalledWith({
        name: 'newseller',
        store_url: STORE,
      }),
    )
    expect(await screen.findByRole('combobox', { name: 'Seller' })).toHaveValue('9')
    await user.click(screen.getByRole('button', { name: /create purchase/i }))
    await waitFor(() =>
      expect(api.createPurchaseOrder).toHaveBeenCalledWith(
        expect.objectContaining({ seller_id: 9 }),
      ),
    )
  })

  it('asks for the store first and proposes the name from its address', async () => {
    const user = userEvent.setup()
    api.createSeller.mockResolvedValue({
      id: 9,
      name: 'drh9989',
      store_url: 'https://www.ebay.com/usr/drh9989',
    })
    api.createPurchaseOrder.mockResolvedValue(FRESH_PURCHASE)
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Seller' }),
      '__add__',
    )
    const store = screen.getByRole('textbox', { name: 'Store web address or email' })
    const name = screen.getByPlaceholderText('Seller name')
    // The store comes before the name, and has the cursor.
    expect(store.compareDocumentPosition(name)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(store).toHaveFocus()

    await user.type(store, 'https://www.ebay.com/usr/drh9989')
    expect(name).toHaveValue('drh9989')

    await user.click(screen.getByRole('button', { name: 'Add seller' }))
    await waitFor(() =>
      expect(api.createSeller).toHaveBeenCalledWith({
        name: 'drh9989',
        store_url: 'https://www.ebay.com/usr/drh9989',
      }),
    )
  })

  it('proposes no name from a mail address, and keeps one typed by hand', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />)
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Seller' }),
      '__add__',
    )
    const store = screen.getByRole('textbox', { name: 'Store web address or email' })
    const name = screen.getByPlaceholderText('Seller name')

    await user.type(store, 'coins@example.com')
    expect(name).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Add seller' })).toBeDisabled()

    await user.type(name, 'By Mail Coins')
    await user.clear(store)
    await user.type(store, 'https://www.ebay.com/usr/someone')
    expect(name).toHaveValue('By Mail Coins')
  })

  it('shows a seller with no store as a name, not a link', async () => {
    api.getPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      id: 22,
      seller_id: 6,
      seller: 'deswin3834',
      seller_url: null,
    })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    expect(await screen.findByText(/deswin3834/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'deswin3834' })).toBeNull()
  })

  it('is changed and cleared under Edit details', async () => {
    const user = userEvent.setup()
    const purchase = { ...FRESH_PURCHASE, id: 22, seller_id: null, source_text: null }
    api.getPurchaseOrder.mockResolvedValue(purchase)
    api.updatePurchaseOrder.mockResolvedValue({
      ...purchase,
      seller_id: 5,
      seller: 'coind0g',
    })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    const pick = await screen.findByRole('combobox', { name: 'Seller' })
    await user.selectOptions(pick, '5')
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    await waitFor(() =>
      expect(api.updatePurchaseOrder).toHaveBeenCalledWith(22, { seller_id: 5 }),
    )
  })

  it('is cleared by choosing none', async () => {
    const user = userEvent.setup()
    const purchase = { ...FRESH_PURCHASE, id: 22, seller_id: 5, seller: 'coind0g' }
    api.getPurchaseOrder.mockResolvedValue(purchase)
    api.updatePurchaseOrder.mockResolvedValue({
      ...purchase,
      seller_id: null,
      seller: null,
    })
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    await user.click(await screen.findByRole('button', { name: 'Edit details' }))
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Seller' }),
      '',
    )
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    await waitFor(() =>
      expect(api.updatePurchaseOrder).toHaveBeenCalledWith(22, { seller_id: null }),
    )
  })
})

describe("NewPurchase: a lot's page as the item's listing", () => {
  const LOT = 'https://hibid.com/lot/280623476/1986-2024-american-eagle'

  it("offers an auction purchase's address to the new item", async () => {
    api.getPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      id: 40,
      vendor: 'hibid.com',
      source_url: LOT,
    })
    api.createInventoryItem.mockResolvedValue({ id: 60, item_code: 'CC-000060' })
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />, { route: '/?order=40' })
    await user.type(await screen.findByRole('textbox', { name: /title/i }), 'Lot 12')
    await user.click(screen.getByRole('button', { name: 'Add item' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenCalledWith(
        expect.objectContaining({ purchase_order_id: 40, listing_url: LOT }),
      ),
    )
  })

  it("does not offer an eBay order's page, which is no listing", async () => {
    api.getPurchaseOrder.mockResolvedValue({
      ...FRESH_PURCHASE,
      id: 41,
      vendor: 'ebay.com',
      source_url: 'https://order.ebay.com/ord/show?orderId=1',
    })
    api.createInventoryItem.mockResolvedValue({ id: 61, item_code: 'CC-000061' })
    const user = userEvent.setup()
    renderWithProviders(<NewPurchase />, { route: '/?order=41' })
    await user.type(await screen.findByRole('textbox', { name: /title/i }), 'A dime')
    await user.click(screen.getByRole('button', { name: 'Add item' }))

    await waitFor(() => expect(api.createInventoryItem).toHaveBeenCalledTimes(1))
    expect(api.createInventoryItem.mock.calls[0][0]).not.toHaveProperty('listing_url')
  })
})

describe('NewPurchase: fixing an item already entered', () => {
  const WITH_LINE = {
    ...FRESH_PURCHASE,
    lines: [
      {
        id: 501,
        item_code: 'CC-000501',
        source_title: '1942 dime',
        description: '',
        item_kind: 'coin',
        item_cost: '3.00',
        status: 'ordered',
      },
    ],
  }

  it('opens the editor from the item code, and shows the fix once saved', async () => {
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(WITH_LINE)
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })

    await user.click(await screen.findByRole('button', { name: 'CC-000501' }))
    expect(screen.getByRole('dialog', { name: 'Edit item' })).toHaveTextContent(
      'editing item 501',
    )
    const reads = api.getPurchaseOrder.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'stub save' }))

    expect(screen.queryByRole('dialog', { name: 'Edit item' })).toBeNull()
    await waitFor(() =>
      expect(api.getPurchaseOrder.mock.calls.length).toBeGreaterThan(reads),
    )
  })

  it('reads the purchase again when the editor is closed without a save', async () => {
    // Errors save on their own inside the editor.
    const user = userEvent.setup()
    api.getPurchaseOrder.mockResolvedValue(WITH_LINE)
    renderWithProviders(<NewPurchase />, { route: '/?order=22' })
    await user.click(await screen.findByRole('button', { name: 'CC-000501' }))
    const reads = api.getPurchaseOrder.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'stub close' }))

    expect(screen.queryByRole('dialog', { name: 'Edit item' })).toBeNull()
    await waitFor(() =>
      expect(api.getPurchaseOrder.mock.calls.length).toBeGreaterThan(reads),
    )
  })
})
