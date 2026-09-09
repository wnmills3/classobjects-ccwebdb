import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CartProvider } from './cart'
import { useCart } from './cart-context'

const coin = (id, price, stock) => ({
  id,
  title: `Coin ${id}`,
  price,
  quantity_available: stock,
})

let cart

function Probe() {
  const value = useCart()
  // Captured in an effect, not during render: reassigning a module
  // variable while rendering is a side effect React's rules forbid.
  useEffect(() => {
    cart = value
  })
  return (
    <div>
      <span data-testid="count">{value.count}</span>
      <span data-testid="total">{value.total}</span>
      <span data-testid="lines">{value.lines.length}</span>
    </div>
  )
}

function mount() {
  return render(
    <CartProvider>
      <Probe />
    </CartProvider>,
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => {
  cart = undefined
  localStorage.clear()
})

describe('CartProvider', () => {
  it('starts empty', () => {
    mount()
    expect(screen.getByTestId('count')).toHaveTextContent('0')
    expect(screen.getByTestId('lines')).toHaveTextContent('0')
  })

  it('adds a line and totals it', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 2))
    expect(screen.getByTestId('count')).toHaveTextContent('2')
    expect(screen.getByTestId('total')).toHaveTextContent('20')
  })

  it('never adds more than the stock on hand', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 3), 10))
    expect(screen.getByTestId('count')).toHaveTextContent('3')
  })

  it('accumulates a repeat add onto the existing line', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 1))
    act(() => cart.add(coin(1, '10.00', 5), 2))
    expect(screen.getByTestId('lines')).toHaveTextContent('1')
    expect(screen.getByTestId('count')).toHaveTextContent('3')
  })

  it('changes the quantity of a line without dropping it', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 1))
    act(() => cart.setQuantity(1, 3))
    // The line must survive: setting a quantity is not a removal.
    expect(screen.getByTestId('lines')).toHaveTextContent('1')
    expect(screen.getByTestId('count')).toHaveTextContent('3')
  })

  it('treats a quantity of zero as a removal', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 2))
    act(() => cart.setQuantity(1, 0))
    expect(screen.getByTestId('lines')).toHaveTextContent('0')
  })

  it('clamps a quantity to the stock on hand', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 4), 1))
    act(() => cart.setQuantity(1, 99))
    expect(screen.getByTestId('count')).toHaveTextContent('4')
  })

  it('removes and clears', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 1))
    act(() => cart.add(coin(2, '5.00', 5), 1))
    act(() => cart.remove(1))
    expect(screen.getByTestId('lines')).toHaveTextContent('1')
    act(() => cart.clear())
    expect(screen.getByTestId('lines')).toHaveTextContent('0')
  })

  it('survives a reload', () => {
    mount()
    act(() => cart.add(coin(1, '10.00', 5), 2))
    mount()
    expect(screen.getAllByTestId('count')[1]).toHaveTextContent('2')
  })
})
