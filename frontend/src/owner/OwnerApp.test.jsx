import { screen, within } from '@testing-library/react'
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
    // The unattached-photographs page's only way in. Without this the
    // /photos nav link and route could both be deleted with the suite
    // staying green.
    expect(screen.getByRole('link', { name: /photos/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /new purchase/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^orders$/i })).toBeInTheDocument()
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
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/photos' })
    expect(
      await screen.findByRole('heading', { name: /unattached photographs/i }),
    ).toBeInTheDocument()
  })

  it('links to the Lots page', () => {
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/nowhere' })
    expect(screen.getByRole('link', { name: /^lots$/i })).toBeInTheDocument()
  })

  it('routes /lots to the sales lots page', async () => {
    // The link above proves only that the link renders. Deleting the
    // <Route path="/lots" ...> line leaves it in place and lands the operator
    // on "Page not found", which no assertion on the navigation can see.
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/lots' })
    expect(
      await screen.findByRole('heading', { name: /sales lots/i }),
    ).toBeInTheDocument()
  })

  it('links to the Auctions page, after Lots and nowhere else', () => {
    // Ruling R4: a single flat NavLink after Lots, not a "Selling" nav
    // group -- that restructuring was measured deliberately out of scope
    // for this branch.
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/nowhere' })
    const links = screen.getAllByRole('link').map((link) => link.textContent)
    const lotsIndex = links.indexOf('Lots')
    expect(lotsIndex).toBeGreaterThan(-1)
    expect(links[lotsIndex + 1]).toBe('Auctions')
  })

  it('routes /auctions to the auctions page', async () => {
    renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/auctions' })
    expect(
      await screen.findByRole('heading', { name: /^auctions$/i }),
    ).toBeInTheDocument()
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
