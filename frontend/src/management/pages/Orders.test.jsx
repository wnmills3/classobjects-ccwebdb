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
    listListings: vi.fn(),
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

/** One page as `api.listOrders` answers it: the rows, and how many in all. */
const pageOf = (rows, total = rows.length) => ({ rows, total })

/** The server's answer to a status change: the order, moved on a version. */
function answerStatus(id, status, version) {
  const order = ORDERS.find((o) => o.id === id)
  return Promise.resolve({ ...order, status, version: version + 1 })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listOrders.mockResolvedValue(pageOf(ORDERS))
  api.setOrderStatus.mockImplementation(answerStatus)
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

  it('changes a status, from the version it was read at, and puts the answer in its row', async () => {
    // The version is what lets the server refuse a status chosen from a page
    // read before someone else moved the order on.
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'shipped')

    expect(api.setOrderStatus).toHaveBeenCalledWith(12, 'shipped', 1)
    await waitFor(() =>
      expect(within(rowFor(12)).getByRole('combobox')).toHaveValue('shipped'),
    )
    // The one order is replaced; the list is not read again.
    expect(api.listOrders).toHaveBeenCalledTimes(1)

    // The next change is sent from the version the answer carried.
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'delivered')
    expect(api.setOrderStatus).toHaveBeenLastCalledWith(12, 'delivered', 2)
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
    expect(api.setOrderStatus).toHaveBeenCalledWith(12, 'cancelled', 1)
  })

  it('asks before refunding an unshipped order, whose stock goes back', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'refunded')

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/back on sale/))
    expect(api.setOrderStatus).not.toHaveBeenCalled()
  })

  /** The page over these orders, loaded, with a user to drive it. */
  async function renderOrders(orders) {
    api.listOrders.mockResolvedValue(pageOf(orders))
    const user = userEvent.setup()
    renderWithProviders(<Orders />, {
      auth: adminAuth(),
      route: '/orders',
      strict: true,
    })
    await screen.findByText('Ada Lovelace')
    return user
  }

  /**
   * Choose `status` for order 12 and expect it refused on the page: nothing
   * sent, no confirmation asked, the reason shown, and every option still
   * live -- a greyed-out choice cannot say why.
   */
  async function expectRefusedHere(user, status, reason) {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const select = within(rowFor(12)).getByRole('combobox')
    for (const option of within(select).getAllByRole('option')) {
      expect(option).toBeEnabled()
    }
    expect(select).toBeEnabled()

    await user.selectOptions(select, status)

    expect(await screen.findByText(reason)).toBeInTheDocument()
    expect(api.setOrderStatus).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    expect(within(rowFor(12)).getByRole('combobox')).toHaveValue(ORDERS[0].status)
  }

  it('says why a refund before shipping is refused for a sale made on another platform', async () => {
    // Refunding it would return stock to a listing the sale ended; the
    // server refuses that as it refuses a cancellation.
    const user = await renderOrders([
      { ...ORDERS[0], sales_venue_code: 'ebay', sales_venue_name: 'eBay' },
    ])
    await expectRefusedHere(user, 'refunded', /Sold on eBay.*no stock to return/)
  })

  it('says why a cancelled order cannot be moved on, and sends nothing', async () => {
    const user = await renderOrders([{ ...ORDERS[0], status: 'cancelled' }])
    const select = within(rowFor(12)).getByRole('combobox')
    expect(select).toBeEnabled()

    await user.selectOptions(select, 'paid')

    expect(
      await screen.findByText(/cancelled and its stock has been returned/),
    ).toBeInTheDocument()
    expect(api.setOrderStatus).not.toHaveBeenCalled()
    expect(within(rowFor(12)).getByRole('combobox')).toHaveValue('cancelled')
  })

  it('says why cancelling a sale made on another platform is refused', async () => {
    // The server refuses the transition with a 409 (routers/orders.py); the
    // page says so itself rather than asking to confirm and then failing.
    const user = await renderOrders([
      { ...ORDERS[0], sales_venue_code: 'ebay', sales_venue_name: 'eBay' },
    ])
    await expectRefusedHere(user, 'cancelled', /Sold on eBay.*no stock to return/)
  })

  it('says why cancelling a shop order whose listing has ended is refused', async () => {
    // A lot bought in the shop ends its listing at checkout; the server
    // refuses the cancellation for the same reason as an outside sale.
    const user = await renderOrders([
      {
        ...ORDERS[0],
        items: ORDERS[0].items.map((line) => ({ ...line, listing_ended: true })),
      },
    ])
    await expectRefusedHere(user, 'cancelled', /listing has ended/)
  })

  it('still offers cancel for an ordinary shop order', async () => {
    await renderPage()
    const select = within(rowFor(12)).getByRole('combobox')
    expect(within(select).getByRole('option', { name: /cancelled/i })).toBeEnabled()
  })

  it('does cancel an outside sale that has already shipped', async () => {
    // The refusal the tests above cover is the server's, and the server only
    // refuses a cancellation that would really return stock. A shipped order
    // returns none, so cancelling one is allowed -- it is how a refund is
    // recorded. An `auction_house` sale is created `delivered`, so every one
    // arrives in this state; refusing it here would make that workflow
    // unreachable from the console.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = await renderOrders([
      {
        ...ORDERS[0],
        sales_venue_code: 'heritage',
        sales_venue_name: 'Heritage',
        status: 'delivered',
      },
    ])

    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'cancelled')

    expect(api.setOrderStatus).toHaveBeenCalledWith(12, 'cancelled', 1)
    expect(screen.queryByText(/no stock to return/)).toBeNull()
  })

  it('shows a refusal without losing the list, and reads the page again', async () => {
    api.setOrderStatus.mockRejectedValue(new Error('Order #12 cannot move to that'))
    const user = await renderPage()
    await user.selectOptions(within(rowFor(12)).getByRole('combobox'), 'packed')

    expect(await screen.findByText('Order #12 cannot move to that')).toBeInTheDocument()
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
    // Refused, perhaps because someone else moved it on: where it stands now.
    await waitFor(() => expect(api.listOrders).toHaveBeenCalledTimes(2))
  })

  it('narrows the list to one status on the server', async () => {
    api.listOrders.mockImplementation(({ status }) =>
      Promise.resolve(pageOf(ORDERS.filter((o) => !status || o.status === status))),
    )
    const user = await renderPage()
    expect(api.listOrders).toHaveBeenCalledWith({ status: '', limit: 100, offset: 0 })

    await user.selectOptions(screen.getByLabelText(/show/i), 'cancelled')

    await waitFor(() => expect(screen.queryByText('Ada Lovelace')).toBeNull())
    expect(api.listOrders).toHaveBeenLastCalledWith({
      status: 'cancelled',
      limit: 100,
      offset: 0,
    })
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
  })

  describe('a page at a time', () => {
    //: 250 orders, newest first: ids 250 down to 1.
    const ALL = Array.from({ length: 250 }, (_, i) => ({
      ...ORDERS[1],
      id: 250 - i,
      customer_name: `Customer ${250 - i}`,
      items: [],
    }))

    beforeEach(() => {
      api.listOrders.mockImplementation(({ status, limit, offset }) => {
        const matching = status ? ALL.filter((o) => o.status === status) : ALL
        return Promise.resolve(
          pageOf(matching.slice(offset, offset + limit), matching.length),
        )
      })
    })

    async function renderAll() {
      const user = userEvent.setup()
      renderWithProviders(<Orders />, { auth: adminAuth(), route: '/orders' })
      await screen.findByText('Customer 250')
      return user
    }

    it('says how many of how many are shown, and pages through them', async () => {
      const user = await renderAll()
      expect(screen.getByText('1-100 of 250')).toBeInTheDocument()
      // Nothing newer than the first page: no button for it.
      expect(screen.queryByRole('button', { name: 'Newer' })).toBeNull()

      await user.click(screen.getByRole('button', { name: 'Older' }))
      expect(await screen.findByText('101-200 of 250')).toBeInTheDocument()
      expect(api.listOrders).toHaveBeenLastCalledWith({
        status: '',
        limit: 100,
        offset: 100,
      })
      expect(screen.getByText('Customer 150')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Older' }))
      expect(await screen.findByText('201-250 of 250')).toBeInTheDocument()
      // The last page: nothing older.
      expect(screen.queryByRole('button', { name: 'Older' })).toBeNull()

      await user.click(screen.getByRole('button', { name: 'Newer' }))
      expect(await screen.findByText('101-200 of 250')).toBeInTheDocument()
    })

    it('starts a new status filter from its first page', async () => {
      const user = await renderAll()
      await user.click(screen.getByRole('button', { name: 'Older' }))
      await screen.findByText('101-200 of 250')

      await user.selectOptions(screen.getByLabelText(/show/i), 'paid')

      await waitFor(() =>
        expect(api.listOrders).toHaveBeenLastCalledWith({
          status: 'paid',
          limit: 100,
          offset: 0,
        }),
      )
      expect(await screen.findByText(/no orders/i)).toBeInTheDocument()
    })
  })

  it('says so when there are no orders', async () => {
    api.listOrders.mockResolvedValue(pageOf([]))
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
