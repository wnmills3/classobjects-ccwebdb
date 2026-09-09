import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import OwnerApp from './OwnerApp'
import { adminAuth, anonymousAuth, renderWithProviders } from '../test/helpers'

describe('owner console shell', () => {
  it('sends an anonymous visitor to sign in', () => {
    renderWithProviders(<OwnerApp />, { auth: anonymousAuth(), route: '/' })
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('refuses a signed-in customer without revealing the console', () => {
    const customer = anonymousAuth({
      user: { id: 2, email: 'buyer@example.com', role: 'customer' },
      isAdmin: false,
    })
    renderWithProviders(<OwnerApp />, { auth: customer, route: '/' })
    expect(screen.getByText(/does not have access/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /people/i })).not.toBeInTheDocument()
  })

  // '/nowhere' renders the console chrome and its "Page not found", without
  // mounting any page. That is exactly what these two cases assert -- the
  // navigation an administrator sees. Rendering at '/' would redirect to
  // /inventory/coins, mount InventoryCoins, and call the API; every existing
  // page test vi.mocks the api module, and a factory mock here would have to
  // list every export the inventory page touches and would break the moment
  // it touched one more.
  it('renders the console navigation for an administrator', () => {
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.getByRole('link', { name: /coins/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /people/i })).toBeInTheDocument()
  })

  it('offers sign-in without a guard, so the guard cannot lock everyone out', () => {
    renderWithProviders(<OwnerApp />, { auth: anonymousAuth(), route: '/login' })
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('does not link back to the shop', () => {
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.queryByRole('link', { name: /catalogue/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^cart$/i })).not.toBeInTheDocument()
  })
})
