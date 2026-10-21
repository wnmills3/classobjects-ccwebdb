import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'

import { api } from '../shared/api'
import { useAuth } from '../shared/auth-context'
import { SHOP_NAME, SHOP_OWNER } from '../shared/site'
import { useRequest } from '../shared/useRequest'
import { useCart } from './cart-context'
import DigiCertSeal from './DigiCertSeal'
import Cart from './pages/Cart'
import Catalog from './pages/Catalog'
import CoinDetail from './pages/CoinDetail'
import Login from './pages/Login'
import Orders from './pages/Orders'
import Register from './pages/Register'

/**
 * A page for signed-in shoppers only. Anyone else is sent to sign in, with
 * the page they asked for as `from`, so signing in brings them back to it.
 */
function RequireAuth({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <p className="muted">Loading...</p>
  if (!user) {
    const from = location.pathname + location.search
    return <Navigate to="/login" replace state={{ from }} />
  }
  return children
}

/** The shop's name, which links home on every page, the closed one too. */
function Brand() {
  return (
    <Link to="/" className="brand">
      {SHOP_NAME}
      <span className="brand-sub">Coins &amp; Currency</span>
    </Link>
  )
}

/**
 * The footer every page of the shop ends with: the copyright line when the
 * owner's legal name is set (VITE_SHOP_OWNER), else just the shop's name.
 */
function Footer() {
  return (
    <footer className="footer muted">
      {SHOP_OWNER ? `© ${new Date().getFullYear()} ${SHOP_OWNER}` : SHOP_NAME}
      <DigiCertSeal />
    </footer>
  )
}

/** What a signed-out visitor is shown at any address while the shop is closed. */
function ComingSoon() {
  return (
    <section className="narrow">
      <h1>Coming soon</h1>
      <p>{SHOP_NAME} is getting ready to open its shop of coins and currency.</p>
      <p className="muted">
        Have an account? <Link to="/login">Sign in</Link>.
      </p>
    </section>
  )
}

/**
 * The shop before opening day (SHOP_OPEN=false), for a visitor who is not
 * signed in: sign-in and the coming-soon page, and nothing else -- no
 * catalog, cart or registration, which the API refuses them anyway.
 */
function ClosedShop() {
  return (
    <div className="app">
      <header className="topbar">
        <Brand />
        <div className="account">
          <NavLink to="/login">Sign in</NavLink>
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/login" element={<Login canRegister={false} />} />
          <Route path="*" element={<ComingSoon />} />
        </Routes>
      </main>
      <Footer />
    </div>
  )
}

/**
 * The public shop.
 *
 * There is no admin-only branch here and no console route. A visitor who
 * types a console URL gets the same "not found" as any other unknown path:
 * a "privileges are required" answer would tell a stranger the page exists.
 *
 * It asks first whether the shop is open. A failed answer is taken as open:
 * the API enforces the closed shop on its own, so the worst a wrong guess
 * here can do is show a catalog that then reports the refusal.
 */
export default function StoreApp() {
  const { user, loading, logout } = useAuth()
  const { count } = useCart()
  const shop = useRequest('shop', () => api.getShop())

  if (loading || (shop.data === undefined && !shop.error)) {
    return (
      <div className="app">
        <header className="topbar">
          <Brand />
        </header>
        <main className="content">
          <p className="muted">Loading...</p>
        </main>
      </div>
    )
  }
  if (shop.data?.open === false && !user) return <ClosedShop />

  return (
    <div className="app">
      <header className="topbar">
        <Brand />

        <nav className="nav">
          <NavLink to="/">Catalog</NavLink>
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

      <Footer />
    </div>
  )
}
