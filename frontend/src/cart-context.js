import { createContext, useContext } from 'react'

/**
 * The cart context and the hook that reads it.
 *
 * Separate from the provider for the same reason as auth-context: a module
 * exporting both a component and a non-component defeats Fast Refresh, so a
 * change anywhere in it costs a full reload and the state it was holding.
 */
export const CartContext = createContext(null)

export function useCart() {
  const ctx = useContext(CartContext)
  if (!ctx) throw new Error('useCart must be used inside a CartProvider')
  return ctx
}
