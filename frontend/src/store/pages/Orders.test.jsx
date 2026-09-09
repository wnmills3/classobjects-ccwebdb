import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({
  api: { listOrders: vi.fn(), setOrderStatus: vi.fn() },
}))

import { api } from '../../shared/api'
import Orders from './Orders'
import { adminAuth, renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

describe('Orders', () => {
  it('says so plainly when there are no orders', async () => {
    api.listOrders.mockResolvedValue([])
    renderWithProviders(<Orders />, { auth: adminAuth() })
    expect(await screen.findByText('No orders yet.')).toBeInTheDocument()
  })

  it('lists an order with its number, status and total', async () => {
    api.listOrders.mockResolvedValue([
      {
        id: 42,
        status: 'shipped',
        created_at: '2026-03-14T00:00:00Z',
        total_amount: '250.00',
        items: [],
      },
    ])
    renderWithProviders(<Orders />, { auth: adminAuth() })

    expect(await screen.findByText(/order #42/i)).toBeInTheDocument()
    // The status appears both as the badge and as the selected option of the
    // admin status control, so match all of them rather than requiring one.
    expect(screen.getAllByText('shipped').length).toBeGreaterThan(0)
    expect(screen.getByText('$250.00')).toBeInTheDocument()
  })

  it('surfaces a load failure instead of rendering an empty page', async () => {
    api.listOrders.mockRejectedValue(new Error('service unavailable'))
    renderWithProviders(<Orders />, { auth: adminAuth() })
    expect(await screen.findByText('service unavailable')).toBeInTheDocument()
  })
})
