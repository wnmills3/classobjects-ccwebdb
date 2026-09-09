import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'
import { adminAuth, emptyCart, renderWithProviders } from './test/helpers'

describe('App shell', () => {
  it('renders the brand and the public navigation', () => {
    renderWithProviders(<App />)
    expect(screen.getByRole('link', { name: /ccwebdb/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /catalogue/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^cart$/i })).toBeInTheDocument()
  })

  it('hides Orders from an anonymous visitor', () => {
    renderWithProviders(<App />)
    expect(screen.queryByRole('link', { name: /orders/i })).not.toBeInTheDocument()
  })

  it('shows Orders once someone is signed in', () => {
    renderWithProviders(<App />, { auth: adminAuth() })
    expect(screen.getByRole('link', { name: /orders/i })).toBeInTheDocument()
  })

  it('puts the item count in the cart link only when the cart has something in it', () => {
    renderWithProviders(<App />, { cart: emptyCart({ count: 3 }) })
    expect(screen.getByRole('link', { name: /cart \(3\)/i })).toBeInTheDocument()
  })
})
