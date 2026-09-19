import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Only the method the page is supposed to call. That is deliberate: the page
// called `api.getCoin`, which does not exist on the real client, so every
// visit threw `TypeError: api.getCoin is not a function` before the page
// could render. A mock listing every method would have hidden that; this one
// fails the same way production did.
vi.mock('../../shared/api', () => ({ api: { getCatalogItem: vi.fn() } }))

import { api } from '../../shared/api'
import CoinDetail from './CoinDetail'
import { emptyCart, renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

const COIN = {
  id: 7,
  title: 'Morgan Dollar 1921',
  price: '89.5',
  quantity_available: 2,
  item_code: 'CC-000007',
  grade_display: 'MS64',
  description: 'A lightly toned example.',
}

const show = (options = {}) =>
  renderWithProviders(
    <Routes>
      <Route path="/coin/:id" element={<CoinDetail />} />
    </Routes>,
    { route: '/coin/7', ...options },
  )

describe('CoinDetail', () => {
  it('loads the item named in the URL', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    show()

    expect(await screen.findByText('Morgan Dollar 1921')).toBeInTheDocument()
    // The id from the path, not a default: a page that ignored it would show
    // the right-looking coin for every URL.
    expect(api.getCatalogItem).toHaveBeenCalledWith('7')
  })

  it('shows the price, stock and specifications', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    show()

    expect(await screen.findByText('$89.50')).toBeInTheDocument()
    expect(screen.getByText('2 available')).toBeInTheDocument()
    expect(screen.getByText('CC-000007')).toBeInTheDocument()
    expect(screen.getByText('MS64')).toBeInTheDocument()
  })

  it('adds the coin to the cart', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    const cart = emptyCart()
    show({ cart })

    const button = await screen.findByRole('button', { name: 'Add to cart' })
    button.click()
    expect(cart.add).toHaveBeenCalledWith(COIN, 1)
  })

  it('refuses to add a sold-out coin', async () => {
    api.getCatalogItem.mockResolvedValue({ ...COIN, quantity_available: 0 })
    show()

    const button = await screen.findByRole('button', { name: 'Sold out' })
    expect(button).toBeDisabled()
  })

  it('reports a failed load rather than showing nothing', async () => {
    api.getCatalogItem.mockRejectedValue(new Error('that coin is not for sale'))
    show()
    expect(await screen.findByText('that coin is not for sale')).toBeInTheDocument()
  })
})
