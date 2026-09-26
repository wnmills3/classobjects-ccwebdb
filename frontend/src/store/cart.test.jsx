import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CartProvider } from './cart'
import { useCart } from './cart-context'

const coin = (id, price, stock) => ({
  id,
  title: `Coin ${id}`,
  price,
  quantity_available: stock,
})

/** The cart as a component sees it; `result.current` is the latest value. */
const mount = () => renderHook(() => useCart(), { wrapper: CartProvider }).result

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe('CartProvider', () => {
  it('starts empty', () => {
    const cart = mount()
    expect(cart.current.count).toBe(0)
    expect(cart.current.lines).toHaveLength(0)
    expect(cart.current.total).toBe('0.00')
  })

  it('adds a line and totals it', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 2))
    expect(cart.current.count).toBe(2)
    expect(cart.current.total).toBe('20.00')
  })

  it('totals in cents, never in floats', () => {
    // Three at 19.99 is 59.97; summed as floats it is 59.970000000000006.
    const cart = mount()
    act(() => cart.current.add(coin(1, '19.99', 5), 3))
    expect(cart.current.total).toBe('59.97')
  })

  it('never adds more than the stock on hand', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 3), 10))
    expect(cart.current.count).toBe(3)
  })

  it('accumulates a repeat add onto the existing line', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 1))
    act(() => cart.current.add(coin(1, '10.00', 5), 2))
    expect(cart.current.lines).toHaveLength(1)
    expect(cart.current.count).toBe(3)
  })

  it('changes the quantity of a line without dropping it', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 1))
    act(() => cart.current.setQuantity(1, 3))
    // The line must survive: setting a quantity is not a removal.
    expect(cart.current.lines).toHaveLength(1)
    expect(cart.current.count).toBe(3)
  })

  it('treats a quantity of zero as a removal', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 2))
    act(() => cart.current.setQuantity(1, 0))
    expect(cart.current.lines).toHaveLength(0)
  })

  it('clamps a quantity to the stock on hand', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 4), 1))
    act(() => cart.current.setQuantity(1, 99))
    expect(cart.current.count).toBe(4)
  })

  it('holds whole coins only', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 1))
    act(() => cart.current.setQuantity(1, 2.5))
    expect(cart.current.count).toBe(2)
    expect(cart.current.total).toBe('20.00')
  })

  it('removes and clears', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 1))
    act(() => cart.current.add(coin(2, '5.00', 5), 1))
    act(() => cart.current.remove(1))
    expect(cart.current.lines).toHaveLength(1)
    act(() => cart.current.clear())
    expect(cart.current.lines).toHaveLength(0)
  })

  it('survives a reload', () => {
    const first = mount()
    act(() => first.current.add(coin(1, '10.00', 5), 2))
    const second = mount()
    expect(second.current.count).toBe(2)
  })
})
