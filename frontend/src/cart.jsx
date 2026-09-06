import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const CART_KEY = 'ccwebdb.cart'
const CartContext = createContext(null)

function readCart() {
  try {
    const raw = localStorage.getItem(CART_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

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

  const setQuantity = useCallback((coinId, quantity) => {
    setLines((current) =>
      current
        .map((l) =>
          l.coin.id === coinId
            ? { ...l, quantity: Math.max(0, Math.min(quantity, l.coin.quantity_available_available)) }
            : l,
        )
        .filter((l) => l.quantity > 0),
    )
  }, [])

  const remove = useCallback((coinId) => {
    setLines((current) => current.filter((l) => l.coin.id !== coinId))
  }, [])

  const clear = useCallback(() => setLines([]), [])

  const { count, total } = useMemo(() => {
    let count = 0
    let total = 0
    for (const line of lines) {
      count += line.quantity
      total += Number(line.coin.price) * line.quantity
    }
    return { count, total }
  }, [lines])

  const value = { lines, add, setQuantity, remove, clear, count, total }
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>
}

export function useCart() {
  const ctx = useContext(CartContext)
  if (!ctx) throw new Error('useCart must be used inside a CartProvider')
  return ctx
}
