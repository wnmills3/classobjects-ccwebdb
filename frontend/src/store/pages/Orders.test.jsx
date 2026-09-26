import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({
  api: { listMyOrders: vi.fn() },
}))

import { api } from '../../shared/api'
import Orders from './Orders'
import { adminAuth, customerAuth, renderWithProviders } from '../../test/helpers'

const ORDER = {
  id: 42,
  status: 'shipped',
  placed_at: '2026-03-14T12:00:00Z',
  total_amount: '250.00',
  items: [
    {
      id: 7,
      listing_id: 3,
      title: '1881-S Morgan Silver Dollar',
      quantity: 1,
      unit_price: '250.00',
    },
  ],
}

beforeEach(() => vi.clearAllMocks())

describe('Orders', () => {
  it('says so plainly when there are no orders', async () => {
    api.listMyOrders.mockResolvedValue([])
    renderWithProviders(<Orders />, { auth: customerAuth() })
    expect(await screen.findByText('No orders yet.')).toBeInTheDocument()
  })

  it('lists an order with its number, status, date, contents and total', async () => {
    api.listMyOrders.mockResolvedValue([ORDER])
    renderWithProviders(<Orders />, { auth: customerAuth() })

    expect(await screen.findByText(/order #42/i)).toBeInTheDocument()
    expect(screen.getByText('shipped')).toBeInTheDocument()
    // placed_at is what the API sends; the page read created_at and showed
    // no date at all.
    expect(screen.getByText(/2026/)).toBeInTheDocument()
    expect(screen.getByText('1881-S Morgan Silver Dollar')).toBeInTheDocument()
    expect(screen.getAllByText('$250.00').length).toBeGreaterThan(0)
  })

  it("is the shopper's own orders, and has no admin view even for an administrator", async () => {
    // Everyone's orders, and changing their status, live in the management console.
    api.listMyOrders.mockResolvedValue([ORDER])
    renderWithProviders(<Orders />, { auth: adminAuth() })

    expect(
      await screen.findByRole('heading', { name: 'Your orders' }),
    ).toBeInTheDocument()
    expect(api.listMyOrders).toHaveBeenCalled()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByText(/all orders/i)).toBeNull()
  })

  it('surfaces a load failure instead of rendering an empty page', async () => {
    api.listMyOrders.mockRejectedValue(new Error('service unavailable'))
    renderWithProviders(<Orders />, { auth: customerAuth() })
    expect(await screen.findByText('service unavailable')).toBeInTheDocument()
  })
})
