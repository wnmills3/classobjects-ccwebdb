import { useCallback, useEffect, useMemo, useState } from 'react'

import { fromCents, isMoney, toCents } from '../shared/cents'
import { CartContext } from './cart-context'

const CART_KEY = 'ccwebdb.cart'

/**
 * The cart lines kept in this browser, or an empty cart when there are
 * none, or storage cannot be read, or what it holds is not JSON.
 */
function readCart() {
  try {
    const raw = localStorage.getItem(CART_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

/**
 * Holds the shopper's cart and keeps it in the browser, so it outlasts a
 * reload.
 *
 * A line is a listing and a quantity, one line per listing. `add` and
 * `setQuantity` never take a line past the stock the listing showed, and a
 * quantity of none drops the line. `count` is the items in the cart and
 * `total` their price as a decimal string; both are for display, and
 * checkout prices every line again.
 */
export function CartProvider({ children }) {
  const [lines, setLines] = useState(readCart)

  useEffect(() => {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(lines))
    } catch {
      /* storage unavailable - cart is in-memory for this session only */
    }
  }, [lines])

  const add = useCallback((coin, quantity = 1) => {
    setLines((current) => {
      const existing = current.find((l) => l.coin.id === coin.id)
      const inCart = existing ? existing.quantity : 0
      // Never let the cart exceed what is actually in stock; the backend
      // would reject it at checkout anyway.
      const next = Math.min(inCart + quantity, coin.quantity_available)
      if (next <= 0) return current
      if (existing) {
        return current.map((l) =>
          l.coin.id === coin.id ? { ...l, quantity: next, coin } : l,
        )
      }
      return [...current, { coin, quantity: next }]
    })
  }, [])

  // Whole coins only, between none (a removal) and the stock on hand.
  const setQuantity = useCallback((coinId, quantity) => {
    setLines((current) =>
      current
        .map((l) =>
          l.coin.id === coinId
            ? {
                ...l,
                quantity: Math.max(
                  0,
                  Math.min(Math.floor(quantity), l.coin.quantity_available),
                ),
              }
            : l,
        )
        .filter((l) => l.quantity > 0),
    )
  }, [])

  const remove = useCallback((coinId) => {
    setLines((current) => current.filter((l) => l.coin.id !== coinId))
  }, [])

  const clear = useCallback(() => setLines([]), [])

  // `total` is a decimal string, summed in whole cents: adding prices as
  // floats shows $59.97000000000001 for three coins at $19.99. A price that
  // is not a money amount (a corrupted saved cart) is left out rather than
  // turning the whole total into NaN; checkout prices every line again.
  const { count, total } = useMemo(() => {
    let count = 0
    let cents = 0
    for (const line of lines) {
      count += line.quantity
      if (isMoney(line.coin.price)) cents += toCents(line.coin.price) * line.quantity
    }
    return { count, total: fromCents(cents) }
  }, [lines])

  const value = useMemo(
    () => ({ lines, add, setQuantity, remove, clear, count, total }),
    [lines, add, setQuantity, remove, clear, count, total],
  )
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>
}
