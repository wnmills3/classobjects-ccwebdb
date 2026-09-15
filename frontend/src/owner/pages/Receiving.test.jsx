import { screen, waitFor } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
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

/** Renders the current route so a test can assert the URL a click produced. */
function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

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

describe('opening an order from the URL', () => {
  it('opens the order named by ?order= without needing a pick', async () => {
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=42',
    })
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(42))
  })

  it('puts the picked order in the URL', async () => {
    renderWithProviders(
      <>
        <Receiving />
        <LocationProbe />
      </>,
      { auth: adminAuth(), route: '/receiving' },
    )
    const order = await screen.findByText(/27-1234/)
    order.click()
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/receiving?order=1'),
    )
  })
})

describe('the vendor page link', () => {
  it('links to the vendor page when the order source is a web address', async () => {
    api.getPurchaseOrder.mockResolvedValue({
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      source_url: 'https://www.ebay.com/itm/1',
      lines: [],
    })
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()

    const link = await screen.findByRole('link', { name: 'Vendor page' })
    expect(link).toHaveAttribute('href', 'https://www.ebay.com/itm/1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows no vendor link when the order carries no source url', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()
    await screen.findByText('CC-000412')

    expect(screen.queryByRole('link', { name: 'Vendor page' })).not.toBeInTheDocument()
  })
})
