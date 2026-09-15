import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listOrders: vi.fn(),
    setOrderStatus: vi.fn(),
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
    status: 'paid',
    total_amount: '378.00',
    placed_at: '2026-09-14T15:00:00Z',
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
    status: 'cancelled',
    total_amount: '42.00',
    placed_at: '2026-09-13T15:00:00Z',
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
})
