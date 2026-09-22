import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listOrders: vi.fn(),
    setOrderStatus: vi.fn(),
    listOrderChanges: vi.fn(),
    listCustomers: vi.fn(),
    listUsers: vi.fn(),
    getCatalogItem: vi.fn(),
    listCatalog: vi.fn(),
    createOrderFor: vi.fn(),
    reviseOrder: vi.fn(),
    customerForUser: vi.fn(),
  },
}))

import { api } from '../api'
import Orders from './Orders'
import { adminAuth, renderWithProviders } from '../../test/helpers'

const ORDERS = [
  {
    id: 12,
    customer_id: 5,
    customer_name: 'Ada Lovelace',
    customer_email: 'ada@example.com',
    sales_venue_code: 'store',
    sales_venue_name: 'Web store',
    status: 'paid',
    total_amount: '378.00',
    placed_at: '2026-09-14T15:00:00Z',
    version: 1,
    notes: null,
    placed_by_email: 'admin@example.com',
    payment_adjustment_due: true,
    items: [
      {
        id: 1,
        listing_id: 3,
        title: '1881-S Morgan Silver Dollar',
        quantity: 2,
        unit_price: '189.00',
      },
    ],
  },
  {
    id: 11,
    customer_id: 6,
    customer_name: 'Grace Hopper',
    customer_email: 'grace@example.com',
    sales_venue_code: 'store',
    sales_venue_name: 'Web store',
    status: 'cancelled',
    total_amount: '42.00',
    placed_at: '2026-09-13T15:00:00Z',
    version: 1,
    notes: null,
    placed_by_email: 'grace@example.com',
    payment_adjustment_due: false,
    items: [
      {
        id: 2,
        listing_id: 4,
        title: '1916-D Mercury Dime',
        quantity: 1,
        unit_price: '42.00',
      },
    ],
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  api.listOrders.mockResolvedValue(ORDERS)
  api.setOrderStatus.mockResolvedValue({})
  api.listCustomers.mockResolvedValue([])
  api.listUsers.mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function renderPage() {
  const user = userEvent.setup()
  renderWithProviders(<Orders />, { auth: adminAuth(), route: '/orders' })
  await screen.findByText('Ada Lovelace')
  return user
}

const rowFor = (id) => screen.getByText(`#${id}`).closest('tr')

describe('owner Orders', () => {
  it('lists every order with who placed it, what is in it and its status', async () => {
    await renderPage()
    const row = within(rowFor(12))
    expect(row.getByText('ada@example.com')).toBeInTheDocument()
    expect(row.getByText(/1881-S Morgan Silver Dollar/)).toBeInTheDocument()
    expect(row.getByText(/x 2/)).toBeInTheDocument()
    expect(row.getByText('$378.00')).toBeInTheDocument()
    expect(row.getByRole('combobox', { name: 'Status of order 12' })).toHaveValue(
      'paid',
    )
  })

  it('offers every status the server knows', async () => {
    await renderPage()
    const select = within(rowFor(12)).getByRole('combobox')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value)
    expect(options).toEqual([
      'pending',
      'paid',
      'packed',
      'shipped',
      'delivered',
      'cancelled',
      'refunded',
    ])
  })

  it('changes a status and reloads the list', async () => {
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'shipped')

    expect(api.setOrderStatus).toHaveBeenCalledWith(12, 'shipped')
    await waitFor(() => expect(api.listOrders).toHaveBeenCalledTimes(2))
  })

  it('asks before cancelling, and sends nothing when declined', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'cancelled')

    expect(confirm).toHaveBeenCalled()
    expect(api.setOrderStatus).not.toHaveBeenCalled()
    // The dropdown shows the status the order still has.
    expect(within(rowFor(12)).getByRole('combobox')).toHaveValue('paid')
  })

  it('cancels when confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'cancelled')
    expect(api.setOrderStatus).toHaveBeenCalledWith(12, 'cancelled')
  })

  it('locks the status of a cancelled order', async () => {
    await renderPage()
    expect(within(rowFor(11)).getByRole('combobox')).toBeDisabled()
  })

  it('does not offer cancel for a sale made on another platform', async () => {
    // The server refuses the transition with a 409 (routers/orders.py); the
    // page offering it and then reporting a refusal is the confusing half.
    api.listOrders.mockResolvedValue([
      { ...ORDERS[0], sales_venue_code: 'ebay', sales_venue_name: 'eBay' },
    ])
    renderWithProviders(<Orders />, {
      auth: adminAuth(),
      route: '/orders',
      strict: true,
    })
    await screen.findByText('Ada Lovelace')
    const select = within(rowFor(12)).getByRole('combobox')
    const cancelled = within(select).getByRole('option', { name: /cancelled/i })
    expect(cancelled).toBeDisabled()
  })

  it('does offer cancel for an outside sale that has already shipped', async () => {
    // The refusal the test above covers is the server's, and the server only
    // refuses a cancellation that would really return stock. A shipped order
    // returns none, so cancelling one is allowed -- it is how a refund is
    // recorded. An `auction_house` sale is created `delivered`, so every one
    // arrives in this state; greying the option out made that workflow
    // unreachable from the console.
    api.listOrders.mockResolvedValue([
      {
        ...ORDERS[0],
        sales_venue_code: 'heritage',
        sales_venue_name: 'Heritage',
        status: 'delivered',
      },
    ])
    renderWithProviders(<Orders />, {
      auth: adminAuth(),
      route: '/orders',
      strict: true,
    })
    await screen.findByText('Ada Lovelace')
    const select = within(rowFor(12)).getByRole('combobox')
    const cancelled = within(select).getByRole('option', { name: /cancelled/i })
    expect(cancelled).toBeEnabled()
    // The tooltip goes with the greying, so it must go too -- a disabled
    // reason left on an enabled option is a claim the page no longer makes.
    expect(cancelled).not.toHaveAttribute('title')
  })

  it('shows a refusal without losing the list', async () => {
    api.setOrderStatus.mockRejectedValue(new Error('Order #12 cannot move to that'))
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'packed')

    expect(await screen.findByText('Order #12 cannot move to that')).toBeInTheDocument()
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
  })

  it('narrows the list to one status', async () => {
    const user = await renderPage()
    await user.selectOptions(screen.getByLabelText(/show/i), 'cancelled')

    expect(screen.queryByText('Ada Lovelace')).toBeNull()
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
  })

  it('says so when there are no orders', async () => {
    api.listOrders.mockResolvedValue([])
    renderWithProviders(<Orders />, { auth: adminAuth(), route: '/orders' })
    expect(await screen.findByText(/no orders/i)).toBeInTheDocument()
  })

  it('reports a failed load', async () => {
    api.listOrders.mockRejectedValue(new Error('cannot reach the server'))
    renderWithProviders(<Orders />, { auth: adminAuth(), route: '/orders' })
    expect(await screen.findByText('cannot reach the server')).toBeInTheDocument()
  })

  it('offers Edit only on pending or paid orders', async () => {
    await renderPage()
    expect(within(rowFor(12)).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(rowFor(11)).queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('opens the editor for a new order', async () => {
    const user = await renderPage()
    await user.click(screen.getByRole('button', { name: 'New order' }))
    expect(
      await screen.findByRole('heading', { name: 'New order' }),
    ).toBeInTheDocument()
  })

  it('flags a payment adjustment and who entered an order', async () => {
    await renderPage()
    expect(within(rowFor(12)).getByText('payment adjustment due')).toBeInTheDocument()
    expect(
      within(rowFor(12)).getByText('entered by admin@example.com'),
    ).toBeInTheDocument()
    expect(within(rowFor(11)).queryByText(/entered by/)).toBeNull()
  })

  it('opens an order history', async () => {
    api.listOrderChanges.mockResolvedValue([])
    const user = await renderPage()
    await user.click(within(rowFor(12)).getByRole('button', { name: 'History' }))
    expect(api.listOrderChanges).toHaveBeenCalledWith(12)
  })
})
