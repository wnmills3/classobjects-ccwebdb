import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { useMemo, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../shared/api', () => ({
  api: {
    listCatalog: vi
      .fn()
      .mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 }),
    listMyOrders: vi.fn().mockResolvedValue([]),
  },
}))

import StoreApp from './StoreApp'
import { AuthContext } from '../shared/auth-context'
import {
  adminAuth,
  anonymousAuth,
  customerAuth,
  emptyCart,
  renderWithProviders,
} from '../test/helpers'

/** An auth context whose login signs a customer in, as the real one does. */
function SignsIn({ children }) {
  const [user, setUser] = useState(null)
  const value = useMemo(
    () => anonymousAuth({ user, login: async () => setUser(customerAuth().user) }),
    [user],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

describe('shop shell', () => {
  it('renders the brand and the public navigation', () => {
    renderWithProviders(<StoreApp />)
    expect(screen.getByRole('link', { name: /ccwebdb/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /catalog/i })).toBeInTheDocument()
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

  it('returns to the page that asked for sign-in', async () => {
    // The guard sent a visitor to /login and forgot where from, so signing in
    // from a bookmarked /orders landed on the catalog.
    const user = userEvent.setup()
    renderWithProviders(
      <SignsIn>
        <StoreApp />
      </SignsIn>,
      { route: '/orders' },
    )

    await user.type(screen.getByLabelText(/email/i), 'buyer@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByRole('heading', { name: 'Your orders' }),
    ).toBeInTheDocument()
  })

  // Both former owner paths, not just one: the spec names each, and a route
  // left behind would be found by whichever URL nobody thought to assert.
  // adminAuth() deliberately -- for an anonymous visitor a redirect to sign-in
  // would also satisfy "not found", so only an administrator seeing a 404
  // proves the route is absent rather than merely guarded.
  it.each(['/admin/people', '/inventory/coins'])(
    'does not confirm that %s exists',
    (route) => {
      renderWithProviders(<StoreApp />, { auth: adminAuth(), route })
      expect(screen.getByText(/page not found/i)).toBeInTheDocument()
      expect(screen.queryByText(/administrator privileges/i)).not.toBeInTheDocument()
    },
  )
})
