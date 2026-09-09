import { Link, Navigate, NavLink, Route, Routes } from 'react-router-dom'

import { useAuth } from '../shared/auth-context'
import { useCart } from './cart-context'
import Cart from './pages/Cart'
import Catalog from './pages/Catalog'
import CoinDetail from './pages/CoinDetail'
import Login from './pages/Login'
import Orders from './pages/Orders'
import Register from './pages/Register'

/**
 * The public shop.
 *
 * There is no `adminOnly` branch here and no owner route. A visitor who types
 * an owner URL gets the same "not found" as any other unknown path -- the
 * previous "Administrator privileges are required" told a stranger the page
 * existed, which is the leak this split closes.
 */
function RequireAuth({ children }) {
  const { user, loading } = useAuth()
  if (loading) return <p className="muted">Loading...</p>
  if (!user) return <Navigate to="/login" replace />
  return children
}

export default function StoreApp() {
  const { user, logout } = useAuth()
  const { count } = useCart()

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          ccwebdb{/* */}
          <span className="brand-sub">Numismatics &amp; Currency</span>
        </Link>

        <nav className="nav">
          <NavLink to="/">Catalogue</NavLink>
          <NavLink to="/cart">Cart{count > 0 ? ` (${count})` : ''}</NavLink>
          {user && <NavLink to="/orders">Orders</NavLink>}
        </nav>

        <div className="account">
          {user ? (
            <>
              <span className="muted">{user.email}</span>
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
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>

      <footer className="footer muted">
        ccwebdb - development build. Prices and inventory are sample data.
      </footer>
    </div>
  )
}
