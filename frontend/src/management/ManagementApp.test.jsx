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
    // The guard sent the owner to /login and forgot where from, so a
    // bookmarked console page came back as the coins list after signing in.
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
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.getByRole('link', { name: /coins/i })).toBeInTheDocument()
    // The unattached-photographs page's only way in. Without this the
    // /photos nav link and route could both be deleted with the suite
    // staying green.
    expect(screen.getByRole('link', { name: /photos/i })).toBeInTheDocument()
    // Purchases and Sales, not "New purchase" and "Orders": an order is
    // both, and the owner read Orders as purchases (2026-09-24).
    expect(screen.getByRole('link', { name: /^purchases$/i })).toHaveAttribute(
      'href',
      '/purchases',
    )
    expect(screen.getByRole('link', { name: /^sales$/i })).toHaveAttribute(
      'href',
      '/sales',
    )
    expect(screen.getByRole('link', { name: /people/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /listings/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /platforms/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /vocabularies/i })).toBeInTheDocument()
    // The spec's Selling group holds exactly the three selling pages.
    const selling = screen.getByRole('group', { name: 'Selling' })
    expect(
      within(selling)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['Listings', 'Lots', 'Auctions'])
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

  it('links to the Lots page', () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.getByRole('link', { name: /^lots$/i })).toBeInTheDocument()
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

  it('links to the Auctions page, after Lots and nowhere else', () => {
    // Ruling R4: a single flat NavLink after Lots, not a "Selling" nav
    // group -- that restructuring was measured deliberately out of scope
    // for this branch.
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    const links = screen.getAllByRole('link').map((link) => link.textContent)
    const lotsIndex = links.indexOf('Lots')
    expect(lotsIndex).toBeGreaterThan(-1)
    expect(links[lotsIndex + 1]).toBe('Auctions')
  })

  it('routes /auctions to the auctions page', async () => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/auctions' })
    expect(
      await screen.findByRole('heading', { name: /^auctions$/i }),
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
