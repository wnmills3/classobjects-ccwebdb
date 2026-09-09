import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({ api: { listCatalog: vi.fn() } }))

import { api } from '../../shared/api'
import Catalog from './Catalog'
import { renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

const page = (items) => ({ items, total: items.length })

describe('Catalog', () => {
  it('renders an item with its formatted price', async () => {
    api.listCatalog.mockResolvedValue(
      page([{ id: 1, title: 'Morgan Dollar 1921', price: '89.5', in_stock: true }]),
    )
    renderWithProviders(<Catalog />)

    expect(await screen.findByText('Morgan Dollar 1921')).toBeInTheDocument()
    expect(screen.getByText('$89.50')).toBeInTheDocument()
  })

  it('says nothing matched rather than showing a blank page', async () => {
    api.listCatalog.mockResolvedValue(page([]))
    renderWithProviders(<Catalog />)
    expect(await screen.findByText('No items match those filters.')).toBeInTheDocument()
  })

  it('offers an in-stock filter', async () => {
    api.listCatalog.mockResolvedValue(page([]))
    renderWithProviders(<Catalog />)
    expect(await screen.findByLabelText(/in stock only/i)).toBeInTheDocument()
  })

  it('reports a failed load', async () => {
    api.listCatalog.mockRejectedValue(new Error('catalogue is unavailable'))
    renderWithProviders(<Catalog />)
    expect(await screen.findByText('catalogue is unavailable')).toBeInTheDocument()
  })
})
