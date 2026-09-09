import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({
  api: {
    listCatalog: vi.fn(),
    createCatalogItem: vi.fn(),
    updateCatalogItem: vi.fn(),
    deleteCatalogItem: vi.fn(),
  },
}))

import { api } from '../../shared/api'
import AdminCoins from './AdminCoins'
import { adminAuth, renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

describe('AdminCoins', () => {
  it('renders the add-item form', async () => {
    api.listCatalog.mockResolvedValue({ items: [] })
    renderWithProviders(<AdminCoins />, { auth: adminAuth() })

    expect(
      await screen.findByRole('heading', { name: /add an item/i }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument()
  })

  it('lists existing catalogue items with their price', async () => {
    api.listCatalog.mockResolvedValue({
      items: [{ id: 7, title: 'Buffalo Nickel', price: '42.00', is_active: true }],
    })
    renderWithProviders(<AdminCoins />, { auth: adminAuth() })

    expect(await screen.findByText('Buffalo Nickel')).toBeInTheDocument()
    expect(screen.getByText('$42.00')).toBeInTheDocument()
  })

  it('asks for inactive items too, so withdrawn ones stay visible to staff', async () => {
    api.listCatalog.mockResolvedValue({ items: [] })
    renderWithProviders(<AdminCoins />, { auth: adminAuth() })

    await screen.findByRole('heading', { name: /add an item/i })
    expect(api.listCatalog).toHaveBeenCalledWith(
      expect.objectContaining({ include_inactive: true }),
    )
  })

  it('reports a failed load', async () => {
    api.listCatalog.mockRejectedValue(new Error('catalogue unavailable'))
    renderWithProviders(<AdminCoins />, { auth: adminAuth() })
    expect(await screen.findByText('catalogue unavailable')).toBeInTheDocument()
  })
})
