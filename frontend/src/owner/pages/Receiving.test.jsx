import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    listStorageLocations: vi.fn(),
    receiveItems: vi.fn(),
  },
}))

import { api } from '../api'
import Receiving from './Receiving'
import { adminAuth, renderWithProviders } from '../../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
  api.listPurchaseOrders.mockResolvedValue([
    {
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      outstanding: 3,
      total: 5,
    },
  ])
  api.listStorageLocations.mockResolvedValue([
    { id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' },
  ])
  api.getPurchaseOrder.mockResolvedValue({
    id: 1,
    order_number: '27-1234',
    vendor: 'eBay',
    ordered_on: '2026-08-30',
    lines: [
      {
        id: 412,
        item_code: 'CC-000412',
        description: '1881-S Morgan $1',
        item_cost: '84.00',
        status: 'ordered',
      },
      {
        id: 413,
        item_code: 'CC-000413',
        description: '1923 Peace $1',
        item_cost: '91.00',
        status: 'received',
      },
    ],
  })
})

describe('Receiving', () => {
  it('offers the orders that still have something outstanding', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    expect(await screen.findByText(/27-1234/)).toBeInTheDocument()
    expect(screen.getByText(/3 of 5/i)).toBeInTheDocument()
  })

  it('lists every line on the order, including one that has already arrived', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(1))
    expect(await screen.findByText('CC-000412')).toBeInTheDocument()
    // Shown for context, but not selectable -- OrderLines.test.jsx covers
    // that in detail; this only checks the two are wired together.
    expect(await screen.findByText('CC-000413')).toBeInTheDocument()
  })
})
