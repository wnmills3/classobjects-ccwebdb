import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({ api: { createOrder: vi.fn() } }))

import { api } from '../../shared/api'
import Cart from './Cart'
import { customerAuth, emptyCart, renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

const COIN = {
  id: 7,
  title: 'Morgan Dollar 1921',
  price: '10.00',
  currency: 'USD',
  quantity_available: 5,
}

function renderCart(options = {}) {
  const cart = emptyCart({
    lines: [{ coin: COIN, quantity: 2 }],
    count: 2,
    total: '20.00',
  })
  renderWithProviders(<Cart />, { cart, ...options })
  return cart
}

describe('Cart quantity', () => {
  it('keeps the line while its quantity box is cleared to type another', async () => {
    // A cleared box read as quantity 0 would be a removal: the line would
    // vanish between deleting "2" and typing "3".
    const user = userEvent.setup()
    const cart = renderCart()
    const box = screen.getByRole('spinbutton')

    await user.clear(box)
    expect(cart.setQuantity).not.toHaveBeenCalled()

    await user.type(box, '3')
    expect(cart.setQuantity).toHaveBeenCalledWith(7, 3)
  })

  it('never sets a fractional quantity', async () => {
    const user = userEvent.setup()
    const cart = renderCart()
    const box = screen.getByRole('spinbutton')

    await user.clear(box)
    await user.type(box, '1.5')

    for (const [, quantity] of cart.setQuantity.mock.calls) {
      expect(Number.isInteger(quantity)).toBe(true)
    }
  })

  it('shows the quantity in the cart again when the box is left empty', async () => {
    const user = userEvent.setup()
    renderCart()
    const box = screen.getByRole('spinbutton')

    await user.clear(box)
    await user.tab()

    expect(box).toHaveValue(2)
  })

  it('does not go on showing a number past the stock, and says what the stock is', async () => {
    // The cart holds a line to the stock on hand. A box still reading 9
    // beside a line total for fewer is two different quantities on one row.
    const user = userEvent.setup()
    renderCart()
    const box = screen.getByRole('spinbutton')

    await user.clear(box)
    await user.type(box, '9')

    expect(box).not.toHaveValue(9)
    expect(screen.getByText('Only 5 available.')).toBeInTheDocument()
  })

  it('says nothing about stock for a quantity within it', async () => {
    const user = userEvent.setup()
    renderCart()
    const box = screen.getByRole('spinbutton')

    await user.clear(box)
    await user.type(box, '4')

    expect(box).toHaveValue(4)
    expect(screen.queryByText(/^Only \d+ available\.$/)).toBeNull()
  })
})

describe('Cart checkout', () => {
  it('orders each line by its listing and quantity', async () => {
    const user = userEvent.setup()
    api.createOrder.mockResolvedValue({ id: 42, total_amount: '20.00' })
    renderCart({ auth: customerAuth() })

    await user.click(screen.getByRole('button', { name: 'Place order' }))

    expect(api.createOrder).toHaveBeenCalledWith([{ listing_id: 7, quantity: 2 }])
  })

  it('empties the cart and names the order once it is placed', async () => {
    const user = userEvent.setup()
    api.createOrder.mockResolvedValue({ id: 42, total_amount: '20.00' })
    const cart = renderCart({ auth: customerAuth() })

    await user.click(screen.getByRole('button', { name: 'Place order' }))

    expect(await screen.findByText(/Order #42 was placed for \$20\.00/)).toBeVisible()
    expect(cart.clear).toHaveBeenCalledTimes(1)
  })

  it('keeps the cart and shows why when the order is refused', async () => {
    const user = userEvent.setup()
    api.createOrder.mockRejectedValue(new Error('Only 1 of listing 7 remain'))
    const cart = renderCart({ auth: customerAuth() })

    await user.click(screen.getByRole('button', { name: 'Place order' }))

    expect(await screen.findByText('Only 1 of listing 7 remain')).toBeInTheDocument()
    expect(cart.clear).not.toHaveBeenCalled()
    // Still the cart, with its button back, so the order can be tried again.
    expect(screen.getByRole('button', { name: 'Place order' })).toBeEnabled()
  })

  it('sends a visitor to sign in, to come back to the cart', async () => {
    // Without `from`, signing in lands on the catalog and the cart they were
    // checking out has to be found again.
    const user = userEvent.setup()

    /** Stands in for the sign-in page: says where it was asked to return to. */
    function CameFrom() {
      return <p>sign in, then {useLocation().state?.from}</p>
    }
    const cart = emptyCart({
      lines: [{ coin: COIN, quantity: 2 }],
      count: 2,
      total: '20.00',
    })
    renderWithProviders(
      <Routes>
        <Route path="/cart" element={<Cart />} />
        <Route path="/login" element={<CameFrom />} />
      </Routes>,
      { cart, route: '/cart' },
    )

    expect(screen.queryByRole('button', { name: 'Place order' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Sign in to check out' }))

    expect(screen.getByText('sign in, then /cart')).toBeInTheDocument()
    expect(api.createOrder).not.toHaveBeenCalled()
  })
})
