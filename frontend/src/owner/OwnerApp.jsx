import { Navigate, NavLink, Route, Routes } from 'react-router-dom'

import { useAuth } from '../shared/auth-context'
import AdminPeople from './pages/AdminPeople'
import Auctions from './pages/Auctions'
import { InventoryCoins, InventoryCurrency } from './pages/Inventory'
import Listings from './pages/Listings'
import Login from './pages/Login'
import Lots from './pages/Lots'
import NewPurchase from './pages/NewPurchase'
import Orders from './pages/Orders'
import Photos from './pages/Photos'
import Platforms from './pages/Platforms'
import Receiving from './pages/Receiving'
import Vocabularies from './pages/Vocabularies'

/**
 * Guard for the whole console rather than for each route.
 *
 * Per-route guarding is fine at four routes and is the pattern that leaks at
 * page five, because a new page is left unguarded by *omission* -- a failure
 * that does not announce itself. Sign-in is the one route outside the guard,
 * since a guard covering it would lock everyone out permanently.
 */
function RequireAdmin({ children }) {
  const { user, loading, isAdmin } = useAuth()
  if (loading) return <p className="muted">Loading...</p>
  if (!user) return <Navigate to="/login" replace />
  if (!isAdmin) {
    return <p className="error">This account does not have access to the console.</p>
  }
  return children
}

function Console() {
  const { user, logout } = useAuth()

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          ccwebdb{/* */}
          <span className="brand-sub">Console</span>
        </span>

        <nav className="nav">
          <NavLink to="/inventory/coins">Coins</NavLink>
          <NavLink to="/inventory/currency">Currency</NavLink>
          <NavLink to="/photos">Photos</NavLink>
          <NavLink to="/receiving">Receive</NavLink>
          <NavLink to="/purchases/new">New purchase</NavLink>
          <NavLink to="/orders">Orders</NavLink>
          <NavLink to="/people">People</NavLink>
          <NavLink to="/listings">Listings</NavLink>
          <NavLink to="/lots">Lots</NavLink>
          <NavLink to="/auctions">Auctions</NavLink>
          <NavLink to="/platforms">Platforms</NavLink>
          <NavLink to="/vocabularies">Vocabularies</NavLink>
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
          <Route path="/receiving" element={<Receiving />} />
          <Route path="/purchases/new" element={<NewPurchase />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/people" element={<AdminPeople />} />
          <Route path="/listings" element={<Listings />} />
          <Route path="/lots" element={<Lots />} />
          <Route path="/auctions" element={<Auctions />} />
          <Route path="/platforms" element={<Platforms />} />
          <Route path="/vocabularies" element={<Vocabularies />} />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  )
}

export default function OwnerApp() {
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
