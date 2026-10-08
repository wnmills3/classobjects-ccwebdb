import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from '../shared/auth-context'
import { HelpBar, HelpProvider } from './HelpBar'
import AdminPeople from './pages/AdminPeople'
import Auctions from './pages/Auctions'
import { InventoryCoins, InventoryCurrency } from './pages/Inventory'
import Listings from './pages/Listings'
import Lists from './pages/Lists'
import Login from './pages/Login'
import Lots from './pages/Lots'
import NewPurchase from './pages/NewPurchase'
import OrderLookup from './pages/OrderLookup'
import Orders from './pages/Orders'
import Photos from './pages/Photos'
import Platforms from './pages/Platforms'
import Receiving from './pages/Receiving'
import Reports from './pages/Reports'
import SpotPrices from './pages/SpotPrices'
import Vocabularies from './pages/Vocabularies'

//: The console's menu, grouped by what the work is for rather than listed
//: flat: what is held, buying it, selling it, the reports over all of it,
//: and the setup the rest relies on. Each group is a label and its pages,
//: in the order the work is done -- a purchase is entered before it is
//: received; an item is listed, grouped into a lot or an auction, and
//: then sold. A labelled group rather than a menu that opens: every page
//: is one click away, and a screen reader announces the group.
const MENU = [
  [
    'Inventory',
    [
      ['/inventory/coins', 'Coins'],
      ['/inventory/currency', 'Currency'],
      ['/photos', 'Photos'],
      ['/order-lookup', 'Order lookup'],
    ],
  ],
  [
    'Procurement',
    [
      ['/purchases', 'Purchases'],
      ['/receiving', 'Receive'],
    ],
  ],
  [
    'Selling',
    [
      ['/listings', 'Listings'],
      ['/lots', 'Lots'],
      ['/auctions', 'Auctions'],
      ['/sales', 'Sales'],
      ['/platforms', 'Platforms'],
      ['/spot-prices', 'Spot prices'],
    ],
  ],
  ['Reports', [['/reports', 'Reports']]],
  [
    'Settings',
    [
      ['/people', 'People'],
      ['/vocabularies', 'Vocabularies'],
      ['/lists', 'Lists'],
    ],
  ],
]

/**
 * Guard for the whole console rather than for each route.
 *
 * Per-route guarding is fine at four routes and is the pattern that leaks at
 * page five, because a new page is left unguarded by *omission* -- a failure
 * that does not announce itself. Sign-in is the one route outside the guard,
 * since a guard covering it would lock everyone out permanently. The page
 * asked for goes along as `from`, so signing in comes back to it.
 */
function RequireAdmin({ children }) {
  const { user, loading, isAdmin } = useAuth()
  const location = useLocation()
  if (loading) return <p className="muted">Loading...</p>
  if (!user) {
    const from = location.pathname + location.search
    return <Navigate to="/login" replace state={{ from }} />
  }
  if (!isAdmin) {
    return <p className="error">This account does not have access to the console.</p>
  }
  return children
}

/**
 * The console shell: the menu, the page, and the help band, in a column the
 * height of the window. Only the page scrolls, so every form fits between
 * the menu and the band and a field's explanation is always in sight.
 */
function Console() {
  const { user, logout } = useAuth()

  return (
    <HelpProvider>
      <div className="app console-app">
        <header className="topbar">
          <span className="brand">
            ccwebdb{/* */}
            <span className="brand-sub">Console</span>
          </span>

          <nav className="nav" aria-label="Console">
            {MENU.map(([group, links]) => (
              <span key={group} className="nav-group" role="group" aria-label={group}>
                <span className="nav-group-label" aria-hidden="true">
                  {group}
                </span>
                <span className="nav-group-links">
                  {links.map(([to, text]) => (
                    <NavLink key={to} to={to}>
                      {text}
                    </NavLink>
                  ))}
                </span>
              </span>
            ))}
          </nav>

          <div className="account">
            <span className="muted">{user.email}</span>
            <button className="link" onClick={logout}>
              Sign out
            </button>
          </div>
        </header>

        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/inventory/coins" replace />} />
            <Route path="/inventory/coins" element={<InventoryCoins />} />
            <Route path="/inventory/currency" element={<InventoryCurrency />} />
            <Route path="/photos" element={<Photos />} />
            <Route path="/order-lookup" element={<OrderLookup />} />
            <Route path="/receiving" element={<Receiving />} />
            <Route path="/purchases" element={<NewPurchase />} />
            {/* The page's earlier address, kept for links and bookmarks. */}
            <Route path="/purchases/new" element={<NewPurchase />} />
            <Route path="/sales" element={<Orders />} />
            {/* The page's earlier address, kept for links and bookmarks. */}
            <Route path="/orders" element={<Orders />} />
            <Route path="/people" element={<AdminPeople />} />
            <Route path="/listings" element={<Listings />} />
            <Route path="/lots" element={<Lots />} />
            <Route path="/auctions" element={<Auctions />} />
            <Route path="/platforms" element={<Platforms />} />
            <Route path="/spot-prices" element={<SpotPrices />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/vocabularies" element={<Vocabularies />} />
            <Route path="/lists" element={<Lists />} />
            <Route path="*" element={<p className="muted">Page not found.</p>} />
          </Routes>
        </main>
        <HelpBar />
      </div>
    </HelpProvider>
  )
}

/**
 * The console's routes: sign-in on its own, and every other address inside
 * the administrator guard, so a page added to the shell is guarded with it.
 */
export default function ManagementApp() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="*"
        element={
          <RequireAdmin>
            <Console />
          </RequireAdmin>
        }
      />
    </Routes>
  )
}
