/**
 * Every address the console answers, and the page each one mounts.
 *
 * A link in the menu proves only that the link renders: deleting a
 * `<Route>` line leaves the link in place and lands the operator on "Page
 * not found", which no assertion on the navigation can see. Each page module
 * is replaced by a stub naming it, so this checks the wiring alone and makes
 * no request; what a page shows is its own test's business.
 */
import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./pages/AdminPeople', () => ({ default: () => 'page:people' }))
vi.mock('./pages/Auctions', () => ({ default: () => 'page:auctions' }))
vi.mock('./pages/Inventory', () => ({
  InventoryCoins: () => 'page:coins',
  InventoryCurrency: () => 'page:currency',
}))
vi.mock('./pages/Listings', () => ({ default: () => 'page:listings' }))
vi.mock('./pages/Lists', () => ({ default: () => 'page:lists' }))
vi.mock('./pages/Lots', () => ({ default: () => 'page:lots' }))
vi.mock('./pages/NewPurchase', () => ({ default: () => 'page:purchases' }))
vi.mock('./pages/OrderLookup', () => ({ default: () => 'page:order-lookup' }))
vi.mock('./pages/Orders', () => ({ default: () => 'page:sales' }))
vi.mock('./pages/Photos', () => ({ default: () => 'page:photos' }))
vi.mock('./pages/Platforms', () => ({ default: () => 'page:platforms' }))
vi.mock('./pages/Receiving', () => ({ default: () => 'page:receiving' }))
vi.mock('./pages/Reports', () => ({ default: () => 'page:reports' }))
vi.mock('./pages/SpotPrices', () => ({ default: () => 'page:spot-prices' }))
vi.mock('./pages/Vocabularies', () => ({ default: () => 'page:vocabularies' }))

import ManagementApp from './ManagementApp'
import { adminAuth, renderWithProviders } from '../test/helpers'

//: Every page the menu links to, with the page its address mounts.
const MENU_PAGES = [
  ['/inventory/coins', 'page:coins'],
  ['/inventory/currency', 'page:currency'],
  ['/photos', 'page:photos'],
  ['/order-lookup', 'page:order-lookup'],
  ['/purchases', 'page:purchases'],
  ['/receiving', 'page:receiving'],
  ['/listings', 'page:listings'],
  ['/lots', 'page:lots'],
  ['/auctions', 'page:auctions'],
  ['/sales', 'page:sales'],
  ['/platforms', 'page:platforms'],
  ['/spot-prices', 'page:spot-prices'],
  ['/reports', 'page:reports'],
  ['/people', 'page:people'],
  ['/vocabularies', 'page:vocabularies'],
  ['/lists', 'page:lists'],
]

//: Addresses kept so links and bookmarks to them still land.
const ALIASES = [
  ['/', 'page:coins'],
  ['/purchases/new', 'page:purchases'],
  ['/orders', 'page:sales'],
]

describe('console routes', () => {
  it.each([...MENU_PAGES, ...ALIASES])('%s mounts %s', (route, page) => {
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route })
    expect(within(screen.getByRole('main')).getByText(page)).toBeInTheDocument()
  })

  it('lists here every page the menu links to', async () => {
    // A page added to the menu without a row above would go unchecked.
    const user = userEvent.setup()
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/nowhere' })
    const menu = screen.getByRole('navigation', { name: 'Console' })
    const hrefs = within(menu)
      .getAllByRole('link')
      .map((link) => link.getAttribute('href'))
    for (const button of within(menu).getAllByRole('button')) {
      await user.click(button)
      const list = screen.getByRole('group', {
        name: button.textContent.replace(/\s*▾$/, ''),
      })
      hrefs.push(
        ...within(list)
          .getAllByRole('link')
          .map((link) => link.getAttribute('href')),
      )
    }
    expect(hrefs.sort()).toEqual(MENU_PAGES.map(([route]) => route).sort())
  })

  it('marks Sales in the menu when the page is reached by its old address', async () => {
    // `/orders` is the Sales page: the menu must still say where the console is.
    const user = userEvent.setup()
    renderWithProviders(<ManagementApp />, { auth: adminAuth(), route: '/orders' })
    const menu = screen.getByRole('navigation', { name: 'Console' })
    const selling = within(menu).getByRole('button', { name: /^Selling/ })
    expect(selling).toHaveClass('current')
    await user.click(selling)
    expect(screen.getByRole('link', { name: 'Sales' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })
})
