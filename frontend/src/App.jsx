import { Link, Navigate, NavLink, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth'
import { useCart } from './cart'
import AdminCoins from './pages/AdminCoins'
import Cart from './pages/Cart'
import Catalog from './pages/Catalog'
import CoinDetail from './pages/CoinDetail'
import { InventoryCoins, InventoryCurrency } from './pages/Inventory'
import Login from './pages/Login'
import Orders from './pages/Orders'
import Register from './pages/Register'

function RequireAuth({ children, adminOnly = false }) {
  const { user, loading, isAdmin } = useAuth()
  if (loading) return <p className="muted">Loading...</p>
  if (!user) return <Navigate to="/login" replace />
  if (adminOnly && !isAdmin) {
    return <p className="error">Administrator privileges are required for this page.</p>
  }
  return children
}

export default function App() {
  const { user, logout, isAdmin } = useAuth()
  const { count } = useCart()

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          ccwebdb
          <span className="brand-sub">Numismatics &amp; Currency</span>
        </Link>

        <nav className="nav">
          <NavLink to="/">Catalogue</NavLink>
          <NavLink to="/cart">Cart{count > 0 ? ` (${count})` : ''}</NavLink>
          {user && <NavLink to="/orders">Orders</NavLink>}
          {isAdmin && <NavLink to="/inventory/coins">Coins</NavLink>}
          {isAdmin && <NavLink to="/inventory/currency">Currency</NavLink>}
          {isAdmin && <NavLink to="/admin/coins">Manage</NavLink>}
        </nav>

        <div className="account">
          {user ? (
            <>
              <span className="muted">
                {user.email}
                {isAdmin && <span className="badge">admin</span>}
              </span>
              <button className="link" onClick={logout}>
                Sign out
              </button>
            </>
          ) : (
            <>
              <NavLink to="/login">Sign in</NavLink>
              <NavLink to="/register">Register</NavLink>
            </>
          )}
        </div>
      </header>

      <main className="content">
        <Routes>
          <Route path="/" element={<Catalog />} />
          <Route path="/coins/:id" element={<CoinDetail />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/cart" element={<Cart />} />
          <Route
            path="/orders"
            element={
              <RequireAuth>
                <Orders />
              </RequireAuth>
            }
          />
          <Route
            path="/inventory/coins"
            element={
              <RequireAuth adminOnly>
                <InventoryCoins />
              </RequireAuth>
            }
          />
          <Route
            path="/inventory/currency"
            element={
              <RequireAuth adminOnly>
                <InventoryCurrency />
              </RequireAuth>
            }
          />
          <Route
            path="/admin/coins"
            element={
              <RequireAuth adminOnly>
                <AdminCoins />
              </RequireAuth>
            }
          />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>

      <footer className="footer muted">
        ccwebdb - development build. Prices and inventory are sample data.
      </footer>
    </div>
  )
}
