import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { useMemo, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

// Only the one call the unattached-photographs page makes on mount. Every
// other case here renders at '/nowhere', which mounts no page at all, so this
// mock does not have to grow into the full surface the note below warns
// about -- and without it the '/photos' case would reach the real fetch.
vi.mock('./api', () => ({
  api: {
    listUnattachedImages: vi.fn().mockResolvedValue([]),
    listLots: vi.fn().mockResolvedValue({ lots: [] }),
    listAuctions: vi.fn().mockResolvedValue({ auctions: [] }),
    listSalesVenues: vi.fn().mockResolvedValue([]),
    listStorageLocations: vi.fn().mockResolvedValue([]),
    listReports: vi.fn().mockResolvedValue([]),
  },
}))

import ManagementApp from './ManagementApp'
import { AuthContext } from '../shared/auth-context'
import {
  adminAuth,
  anonymousAuth,
  customerAuth,
  renderWithProviders,
} from '../test/helpers'

/** An auth context whose login signs an administrator in, as the real one does. */
function SignsIn({ children }) {
  const [user, setUser] = useState(null)
  const value = useMemo(
    () =>
      user
        ? adminAuth()
        : anonymousAuth({ login: async () => setUser(adminAuth().user) }),
    [user],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

describe('management console shell', () => {
  it('sends an anonymous visitor to sign in', () => {
    renderWithProviders(<ManagementApp />, { auth: anonymousAuth(), route: '/' })
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('returns to the page that asked for sign-in', async () => {
    // A guard that sends the owner to /login and forgets where from brings a
    // bookmarked console page back as the coins list after signing in.
    const user = userEvent.setup()
    renderWithProviders(
      <SignsIn>
        <ManagementApp />
      </SignsIn>,
      { route: '/photos' },
    )

    await user.type(screen.getByLabelText(/email/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByRole('heading', { name: /unattached photographs/i }),
    ).toBeInTheDocument()
  })

  it('refuses a signed-in customer without revealing the console', () => {
    renderWithProviders(<ManagementApp />, { auth: customerAuth(), route: '/' })
    expect(screen.getByText(/does not have access/i)).toBeInTheDocument()
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  // '/nowhere' renders the console chrome and its "Page not found", without
  // mounting any page. Rendering at '/' would redirect to /inventory/coins,
  // mount InventoryCoins, and call the API; every existing page test vi.mocks
  // the api module, and a factory mock here would have to list every export
  // the inventory page touches and would break the moment it touched one
  // more. What the menu holds and how it opens is ConsoleMenu's own test.
  it('renders the console menu for an administrator', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    const menu = screen.getByRole('navigation', { name: 'Console' })
    await user.click(within(menu).getByRole('button', { name: /^Procurement/ }))
    // Purchases and Sales, not "New purchase" and "Orders": an order is
    // both, and "Orders" reads as purchases.
    expect(screen.getByRole('link', { name: 'Purchases' })).toHaveAttribute(
      'href',
      '/purchases',
    )
    await user.click(within(menu).getByRole('button', { name: /^Selling/ }))
    expect(screen.getByRole('link', { name: 'Sales' })).toHaveAttribute(
      'href',
      '/sales',
    )
  })

  it('routes /order-lookup to the order lookup page', async () => {
    renderWithProviders(<ManagementApp />, {
      auth: adminAuth(),
      route: '/order-lookup',
    })
    expect(
      await screen.findByRole('heading', { name: 'Order lookup', level: 1 }),
    ).toBeInTheDocument()
  })

  it('routes /photos to the unattached-photographs page', async () => {
    // The nav link above proves only that the link is rendered. This is the
    // other half of the wiring: deleting the <Route path="/photos" ...> line
    // leaves the link in place and lands the operator on "Page not found",
    // which no assertion on the navigation can see.
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/photos' })
    expect(
      await screen.findByRole('heading', { name: /unattached photographs/i }),
    ).toBeInTheDocument()
  })

  it('keeps a help band at the bottom of the console, once', () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    const bands = screen.getAllByRole('contentinfo', { name: 'Field help' })
    expect(bands).toHaveLength(1)
    // The band comes after the page, so it sits below it in the column.
    const main = screen.getByRole('main')
    expect(
      main.compareDocumentPosition(bands[0]) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('routes /lots to the sales lots page', async () => {
    // The link above proves only that the link renders. Deleting the
    // <Route path="/lots" ...> line leaves it in place and lands the operator
    // on "Page not found", which no assertion on the navigation can see.
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/lots' })
    expect(
      await screen.findByRole('heading', { name: /sales lots/i }),
    ).toBeInTheDocument()
  })

  it('routes /auctions to the auctions page', async () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/auctions' })
    expect(
      await screen.findByRole('heading', { name: /^auctions$/i }),
    ).toBeInTheDocument()
  })

  it('routes /reports to the reports page', async () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/reports' })
    expect(
      await screen.findByRole('heading', { name: /^reports$/i, level: 1 }),
    ).toBeInTheDocument()
  })

  it('offers sign-in without a guard, so the guard cannot lock everyone out', () => {
    renderWithProviders(<ManagementApp />, { auth: anonymousAuth(), route: '/login' })
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('does not link back to the shop', () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.queryByRole('link', { name: /catalog/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^cart$/i })).not.toBeInTheDocument()
  })
})
