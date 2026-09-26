import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({ api: { createOrder: vi.fn() } }))

import Cart from './Cart'
import { emptyCart, renderWithProviders } from '../../test/helpers'

const COIN = {
  id: 7,
  title: 'Morgan Dollar 1921',
  price: '10.00',
  currency: 'USD',
  quantity_available: 5,
}

function renderCart() {
  const cart = emptyCart({
    lines: [{ coin: COIN, quantity: 2 }],
    count: 2,
    total: '20.00',
  })
  renderWithProviders(<Cart />, { cart })
  return cart
}

describe('Cart quantity', () => {
  it('keeps the line while its quantity box is cleared to type another', async () => {
    // Clearing the box read as quantity 0, and a quantity of 0 is a removal:
    // the line vanished between deleting "2" and typing "3".
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
})
