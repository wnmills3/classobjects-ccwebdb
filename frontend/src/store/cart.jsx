import { useCallback, useEffect, useMemo, useState } from 'react'

import { onSignedOut } from '../shared/api'
import { fromCents, isMoney, toCents } from '../shared/cents'
import { CartContext } from './cart-context'

// The version is in the key so a cart saved in an older shape is never read
// as this one.
const CART_KEY = 'ccwebdb.cart.v2'

const isCount = (n) => Number.isInteger(n) && n >= 0

/**
 * Whether a saved line has the shape every reader of the cart relies on:
 * a listing with an id, a price and a stock, and a whole quantity of at
 * least one.
 */
function isLine(line) {
  const coin = line?.coin
  return (
    coin !== null &&
    typeof coin === 'object' &&
    Number.isInteger(coin.id) &&
    typeof coin.price === 'string' &&
    isCount(coin.quantity_available) &&
    Number.isInteger(line.quantity) &&
    line.quantity >= 1
  )
}

/**
 * The cart lines kept in this browser, less any line not in a line's shape;
 * an empty cart when there are none, or storage cannot be read, or what it
 * holds is not a list. Storage is anyone's to edit, and a malformed line
 * would otherwise fail every render of the page that shows it.
 */
function readCart() {
  try {
    const saved = JSON.parse(localStorage.getItem(CART_KEY) ?? '[]')
    return Array.isArray(saved) ? saved.filter(isLine) : []
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
 *
 * Signing out empties it, whether on purpose or because the server stopped
 * accepting the session: what one account put in the cart is not left for
 * the next person at the same browser.
 */
export function CartProvider({ children }) {
  const [lines, setLines] = useState(readCart)

  useEffect(() => onSignedOut(() => setLines([])), [])

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
  // floats shows $299.96999999999997 for three coins at $99.99. A price that
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
