import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import StoreApp from './StoreApp'
import { adminAuth, emptyCart, renderWithProviders } from '../test/helpers'

describe('shop shell', () => {
  it('renders the brand and the public navigation', () => {
    renderWithProviders(<StoreApp />)
    expect(screen.getByRole('link', { name: /ccwebdb/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /catalogue/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^cart$/i })).toBeInTheDocument()
  })

  it('hides Orders from an anonymous visitor', () => {
    renderWithProviders(<StoreApp />)
    expect(screen.queryByRole('link', { name: /orders/i })).not.toBeInTheDocument()
  })

  it('shows Orders once someone is signed in', () => {
    renderWithProviders(<StoreApp />, { auth: adminAuth() })
    expect(screen.getByRole('link', { name: /orders/i })).toBeInTheDocument()
  })

  it('puts the item count in the cart link only when the cart has something in it', () => {
    renderWithProviders(<StoreApp />, { cart: emptyCart({ count: 3 }) })
    expect(screen.getByRole('link', { name: /cart \(3\)/i })).toBeInTheDocument()
  })

  it('offers no console navigation, even to an administrator', () => {
    renderWithProviders(<StoreApp />, { auth: adminAuth() })
    expect(screen.queryByRole('link', { name: /manage/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /people/i })).not.toBeInTheDocument()
  })

  it('does not confirm that owner pages exist', () => {
    renderWithProviders(<StoreApp />, { auth: adminAuth(), route: '/admin/people' })
    expect(screen.getByText(/page not found/i)).toBeInTheDocument()
    expect(screen.queryByText(/administrator privileges/i)).not.toBeInTheDocument()
  })
})
