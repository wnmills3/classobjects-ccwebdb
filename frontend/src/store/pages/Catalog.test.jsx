import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({ api: { listCatalog: vi.fn() } }))

import { api } from '../../shared/api'
import Catalog from './Catalog'
import { renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

// The shape `GET /api/catalog` answers with: one page of entries, and where
// that page sits in the whole.
const page = (items) => ({ items, total: items.length, limit: 12, offset: 0 })

describe('Catalog', () => {
  it('renders an item with its formatted price', async () => {
    api.listCatalog.mockResolvedValue(
      page([
        { id: 1, title: 'Morgan Dollar 1921', price: '89.5', quantity_available: 1 },
      ]),
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
    api.listCatalog.mockRejectedValue(new Error('catalog is unavailable'))
    renderWithProviders(<Catalog />)
    expect(await screen.findByText('catalog is unavailable')).toBeInTheDocument()
  })

  it('asks the catalog once for what was typed, not once per keystroke', async () => {
    const user = userEvent.setup()
    api.listCatalog.mockResolvedValue(page([]))
    renderWithProviders(<Catalog />)
    await screen.findByText('No items match those filters.')

    await user.type(
      screen.getByRole('searchbox', { name: 'Search the catalog' }),
      'peace',
    )

    await waitFor(() =>
      expect(api.listCatalog).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'peace' }),
      ),
    )
    const asked = api.listCatalog.mock.calls.map(([params]) => params.q)
    expect(asked).toEqual(['', 'peace'])
  })
})

// A LOT card: no photograph, no country, no year, no grade -- those describe
// one coin, and no single value of any of them describes a group. `members`
// is where all of it is.
const LOT = {
  id: 12,
  title: 'Three Morgan Dollars, 1881-1883',
  price: '1200',
  quantity_available: 1,
  inventory_item_id: null,
  item_code: null,
  country: null,
  year_start: null,
  grade_display: null,
  thumbnail_url: null,
  piece_count: 3,
  members: [
    {
      inventory_item_id: 7,
      item_code: 'CC-000007',
      title: '1881-S Morgan Dollar',
      thumbnail_url: '/media/thumb/aaa.jpg',
    },
    {
      inventory_item_id: 9,
      item_code: 'CC-000009',
      title: '1882-S Morgan Dollar',
      thumbnail_url: null,
    },
    {
      inventory_item_id: 11,
      item_code: 'CC-000011',
      title: '1883-O Morgan Dollar',
      thumbnail_url: null,
    },
  ],
}

describe('a lot in the catalog grid', () => {
  it('says a lot is a lot', async () => {
    // Without this the card carried a blank line where a coin's country,
    // year and grade go: an anonymous box at a group's price, which reads as
    // one very expensive coin.
    api.listCatalog.mockResolvedValue(page([LOT]))
    renderWithProviders(<Catalog />)
    expect(await screen.findByText('Lot of 3 items')).toBeInTheDocument()
  })

  it('shows a lot card with its first coin, not an empty frame', async () => {
    // A lot has no thumbnail of its own by design. The card used to fall
    // through to the "no photograph" hatching for every lot, however many
    // photographed coins were in it.
    api.listCatalog.mockResolvedValue(page([LOT]))
    renderWithProviders(<Catalog />)
    const picture = await screen.findByRole('img', {
      name: '1881-S Morgan Dollar, one of the 3 items in this lot',
    })
    expect(picture).toHaveAttribute('src', '/media/thumb/aaa.jpg')
  })

  it('still describes a single coin by its own attributes', async () => {
    api.listCatalog.mockResolvedValue(
      page([
        {
          id: 1,
          title: 'Morgan Dollar 1921',
          price: '89.5',
          country: 'US',
          year_start: 1921,
          grade_display: 'MS64',
          thumbnail_url: null,
          members: [],
        },
      ]),
    )
    renderWithProviders(<Catalog />)
    expect(await screen.findByText('US - 1921 - MS64')).toBeInTheDocument()
  })
})
