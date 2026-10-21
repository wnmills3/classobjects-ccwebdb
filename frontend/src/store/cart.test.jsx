import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { saveTokens, send, signOut } from '../shared/api'
import { CartProvider } from './cart'
import { useCart } from './cart-context'

const coin = (id, price, stock) => ({
  id,
  title: `Coin ${id}`,
  price,
  quantity_available: stock,
})

//: Where the cart is kept, written out so a renamed key is a test failure.
const CART_KEY = 'ccwebdb.cart.v2'

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
    // Three at 99.99 is 299.97; summed as floats it is 299.96999999999997.
    const cart = mount()
    act(() => cart.current.add(coin(1, '99.99', 5), 3))
    expect(cart.current.total).toBe('299.97')
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

  it('empties when the shopper signs out', () => {
    const cart = mount()
    act(() => cart.current.add(coin(1, '10.00', 5), 2))
    act(() => signOut())
    expect(cart.current.lines).toHaveLength(0)
    // And stays empty across a reload, for the next person at this browser.
    expect(mount().current.lines).toHaveLength(0)
  })

  it('empties when the server finally refuses the session', async () => {
    saveTokens({ access_token: 'old', refresh_token: 'old' })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        statusText: 'Unauthorized',
        text: () => Promise.resolve('{"detail":"Could not validate credentials"}'),
      }),
    )
    try {
      const cart = mount()
      act(() => cart.current.add(coin(1, '10.00', 5), 2))
      await act(() => expect(send('/api/orders?mine=true')).rejects.toThrow())
      expect(cart.current.lines).toHaveLength(0)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('a saved cart', () => {
  const save = (value) => localStorage.setItem(CART_KEY, JSON.stringify(value))

  it('keeps the well-formed lines and drops the rest', () => {
    save([
      { coin: coin(1, '10.00', 5), quantity: 2 },
      { coin: coin(2, '5.00', 5), quantity: 0 },
      { coin: coin(3, '5.00', 5), quantity: 1.5 },
      { coin: { ...coin(4, '5.00', 5), id: '4' }, quantity: 1 },
      { coin: coin(5, 5, 5), quantity: 1 },
      { coin: { id: 6, price: '5.00' }, quantity: 1 },
      { coin: null, quantity: 1 },
      { quantity: 1 },
      null,
      'a line',
    ])
    const cart = mount()
    expect(cart.current.lines.map((l) => l.coin.id)).toEqual([1])
    expect(cart.current.total).toBe('20.00')
  })

  it.each([
    ['an object', { coin: coin(1, '10.00', 5), quantity: 1 }],
    ['a number', 7],
    ['null', null],
  ])('is an empty cart when what is stored is %s, not a list', (_case, value) => {
    save(value)
    expect(mount().current.lines).toEqual([])
  })

  it('is an empty cart when what is stored is not JSON', () => {
    localStorage.setItem(CART_KEY, '[{not json')
    expect(mount().current.lines).toEqual([])
  })

  it('ignores a cart saved under the unversioned key', () => {
    localStorage.setItem(
      'ccwebdb.cart',
      JSON.stringify([{ coin: coin(1, '10.00', 5), quantity: 2 }]),
    )
    expect(mount().current.lines).toEqual([])
  })
})
