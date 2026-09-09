# Owner Console Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the frontend into two Vite entry points -- the public shop and
the owner console -- so the shop bundle contains no owner code and exposes no
owner route.

**Architecture:** One Vite project, three source trees (`shared/`, `store/`,
`owner/`), two HTML entry points. Rollup treats each entry as an independent
module graph. The separation is enforced by ESLint import-boundary rules at
edit time and verified against the built artefact by a check that inspects
which modules ended up in which chunk.

**Tech Stack:** Vite 8, React 19, react-router-dom 7, Vitest 5, ESLint 10
(flat config), Prettier 3.

**Spec:** `docs/specs/owner-console-separation-design.md`

## Global Constraints

- **No backend change.** `require_admin` in `backend/app/deps.py` remains the
  access control. Nothing in this plan touches `backend/`.
- **Everything stays under `frontend/src`.** `sonar-project.properties` points
  `sonar.sources` at `frontend/src` and vitest's coverage `include` is
  `src/**/*.{js,jsx}`. Moving files outside `src/` silently drops them from
  both.
- **cmd/batch only.** No PowerShell in scripts, chat commands, or docs.
- **Write files with the Write tool; never shell heredocs.** Scripted
  multi-file edits must assert their anchor matches before writing.
- **Commit messages go through a file.** Write the message to the scratchpad
  and pass it as `git commit -F <path>`. Never a heredoc, and never a
  multi-line `-m`. Every commit ends with the attribution lines this session
  is configured to use.
- **`node` is not on PATH.** Invoke it as
  `"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe"`, which is what
  `scripts\ccweb_check.cmd` does.
- **`NoDefaultCurrentDirectoryInExePath=1` is set.** Run scripts as
  `.\scripts\ccweb_check.cmd`, never `cmd /c ccweb_check.cmd`.
- **The two applications must never link to each other.** A link from the
  console to the shop is harmless; a link the other way reintroduces the
  discovery this work removes.
- **Branch first.** Work on a topic branch; do not commit to `main`.
- The gate for every task is `.\scripts\ccweb_check.cmd` from the repository
  root. It must exit 0.

---

## File Structure

Final layout. Task 1 creates the trees; later tasks add the files marked NEW.

```
frontend/
  index.html                     MODIFIED  -> /src/store/main.jsx
  owner.html                     NEW       -> /src/owner/main.jsx
  vite.config.js                 MODIFIED  two inputs, manifest, dev fallback
  eslint.config.js               MODIFIED  import-boundary rules
  scripts/check-bundle-isolation.mjs  NEW  chunk-membership assertion
  src/
    shared/    api.js  api.test.js  format.js  format.test.js
               auth.jsx  auth.test.jsx  auth-context.js
               reference.jsx  reference-context.js
               LoginForm.jsx  LoginForm.test.jsx        NEW
               shared.css                               NEW
    store/     main.jsx  StoreApp.jsx  StoreApp.test.jsx  styles.css
               cart.jsx  cart.test.jsx  cart-context.js
               pages/  Catalog  CoinDetail  Cart  Orders  Login  Register
    owner/     main.jsx  OwnerApp.jsx  OwnerApp.test.jsx  styles.css   NEW
               pages/  Login  Inventory  AdminCoins  AdminPeople
                       inventory/  BulkEditBar  FilterPanel
                                   InventoryTable  ItemEditForm
                                   ReviewPane  specs  useInventorySearch
    test/      helpers.jsx  setup.js
```

`src/test/` belongs to neither application. It is imported only by `.test.jsx`
files, which never enter a bundle, so no import-boundary rule is scoped to it.

---

### Task 1: Restructure into shared / store / owner

Pure move plus import rewrite. **No behaviour changes.** The 69 existing tests
are the proof: every one must still pass without editing a single assertion.

**Files:**
- Create: `frontend/src/shared/`, `frontend/src/store/`, `frontend/src/store/pages/`, `frontend/src/owner/`, `frontend/src/owner/pages/`
- Move: 37 files (listed in the script below; 42 entries, 5 of which stay put)
- Modify: `frontend/src/App.jsx`, `frontend/src/main.jsx`, `frontend/src/test/helpers.jsx` (imports only)
- Unmoved by design: `App.jsx`, `App.test.jsx`, `main.jsx`, `styles.css` -- Tasks 2, 3 and 5 deal with those

**Interfaces:**
- Consumes: nothing.
- Produces: every module's new path. Later tasks import
  `../../shared/api`, `../../shared/auth-context`, `../cart-context` and so on
  exactly as this task leaves them.

- [ ] **Step 1: Confirm the starting state is green**

```
.\scripts\ccweb_check.cmd
```

Expected: `All checks passed.` and exit 0. If it is not green now, stop -- a
failure after the move would be impossible to attribute.

- [ ] **Step 2: Write the move script**

Write it as `restructure.py` in the session scratchpad, **not** in the
repository -- it runs once and is not project code. It resolves each
relative import against the file's **old** directory, maps it through
`MODULE_MOVES`, then re-relativises from the file's **new** directory. That is
why it is a script and not a list of substitutions: `../api` means a different
target depending on which file it appears in.

```python
"""Move the frontend into shared/ store/ owner/ and fix every relative import.

Resolution is done per file against its OLD directory and re-relativised from
its NEW one, because the same import text ('../api') resolves differently
depending on where the importing file sits.
"""

import io
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path("C:/Users/wnmil/dev/classobjects-ccwebdb")
SRC = REPO / "frontend" / "src"

#: old src-relative file path -> new src-relative file path.
#: A key equal to its value stays put but still has its imports rewritten.
MOVES = {
    "api.js": "shared/api.js",
    "api.test.js": "shared/api.test.js",
    "format.js": "shared/format.js",
    "format.test.js": "shared/format.test.js",
    "auth.jsx": "shared/auth.jsx",
    "auth.test.jsx": "shared/auth.test.jsx",
    "auth-context.js": "shared/auth-context.js",
    "reference.jsx": "shared/reference.jsx",
    "reference-context.js": "shared/reference-context.js",
    "cart.jsx": "store/cart.jsx",
    "cart.test.jsx": "store/cart.test.jsx",
    "cart-context.js": "store/cart-context.js",
    "pages/Catalog.jsx": "store/pages/Catalog.jsx",
    "pages/Catalog.test.jsx": "store/pages/Catalog.test.jsx",
    "pages/CoinDetail.jsx": "store/pages/CoinDetail.jsx",
    "pages/Cart.jsx": "store/pages/Cart.jsx",
    "pages/Orders.jsx": "store/pages/Orders.jsx",
    "pages/Orders.test.jsx": "store/pages/Orders.test.jsx",
    "pages/Login.jsx": "store/pages/Login.jsx",
    "pages/Login.test.jsx": "store/pages/Login.test.jsx",
    "pages/Register.jsx": "store/pages/Register.jsx",
    "pages/Register.test.jsx": "store/pages/Register.test.jsx",
    "pages/Inventory.jsx": "owner/pages/Inventory.jsx",
    "pages/AdminCoins.jsx": "owner/pages/AdminCoins.jsx",
    "pages/AdminCoins.test.jsx": "owner/pages/AdminCoins.test.jsx",
    "pages/AdminPeople.jsx": "owner/pages/AdminPeople.jsx",
    "pages/AdminPeople.test.jsx": "owner/pages/AdminPeople.test.jsx",
    "pages/inventory/BulkEditBar.jsx": "owner/pages/inventory/BulkEditBar.jsx",
    "pages/inventory/FilterPanel.jsx": "owner/pages/inventory/FilterPanel.jsx",
    "pages/inventory/FilterPanel.test.jsx": "owner/pages/inventory/FilterPanel.test.jsx",
    "pages/inventory/InventoryTable.jsx": "owner/pages/inventory/InventoryTable.jsx",
    "pages/inventory/InventoryTable.test.jsx": "owner/pages/inventory/InventoryTable.test.jsx",
    "pages/inventory/ItemEditForm.jsx": "owner/pages/inventory/ItemEditForm.jsx",
    "pages/inventory/ItemEditForm.test.jsx": "owner/pages/inventory/ItemEditForm.test.jsx",
    "pages/inventory/ReviewPane.jsx": "owner/pages/inventory/ReviewPane.jsx",
    "pages/inventory/specs.js": "owner/pages/inventory/specs.js",
    "pages/inventory/useInventorySearch.js": "owner/pages/inventory/useInventorySearch.js",
    # Stay where they are; imports still need rewriting.
    "App.jsx": "App.jsx",
    "App.test.jsx": "App.test.jsx",
    "main.jsx": "main.jsx",
    "test/helpers.jsx": "test/helpers.jsx",
    "test/setup.js": "test/setup.js",
}

#: Extension-less module path -> new extension-less module path.
MODULES = {
    os.path.splitext(old)[0]: os.path.splitext(new)[0] for old, new in MOVES.items()
}

#: `from './x'` and bare `import './x'`.
IMPORT_RE = re.compile(r"""(from\s+|import\s+)(['"])(\.[^'"]*)\2""")

#: `vi.mock('./x', ...)`. Not an import clause, so IMPORT_RE cannot see it,
#: but it names a module and moves with one. Five test files register an api
#: mock this way and then import the same module normally; rewriting only the
#: import leaves the mock keyed to a path that no longer names what the
#: component loads, so the real module is used and the test fails with
#: "mockResolvedValue is not a function" -- 19 failures, none of which point
#: at the cause.
VI_MOCK_RE = re.compile(r"""(vi\.mock\(\s*)(['"])(\.[^'"]*)\2""")


def rewrite(text: str, old_rel: str, new_rel: str) -> str:
    """Repoint every relative import in one file that has moved old -> new."""
    old_dir = os.path.dirname(old_rel)
    new_dir = os.path.dirname(new_rel)

    def one(match):
        spec = match.group(3)
        # A stylesheet import carries its extension and is not in MODULES.
        # Task 5 splits the stylesheets; leave them alone here.
        if spec.endswith(".css"):
            return match.group(0)
        target = os.path.normpath(os.path.join(old_dir, spec)).replace("\\", "/")
        if target not in MODULES:
            raise KeyError(f"{old_rel}: import {spec!r} -> {target!r} is not in MODULES")
        moved = MODULES[target]
        rel = os.path.relpath(moved, new_dir or ".").replace("\\", "/")
        if not rel.startswith("."):
            rel = "./" + rel
        return f"{match.group(1)}{match.group(2)}{rel}{match.group(2)}"

    return VI_MOCK_RE.sub(one, IMPORT_RE.sub(one, text))


def main() -> int:
    missing = [old for old in MOVES if not (SRC / old).exists()]
    if missing:
        print("FAILED: these files do not exist:")
        for m in missing:
            print("  " + m)
        return 1

    # The map must also be complete in the other direction. A file on disk
    # that MOVES does not mention is never visited, so its imports are left
    # pointing at the old locations and it fails at build time with no clue
    # as to why. Checking both directions turns that into a message here.
    on_disk = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*")
        if p.is_file() and p.suffix in {".js", ".jsx"}
    }
    unaccounted = sorted(on_disk - set(MOVES))
    if unaccounted:
        print("FAILED: on disk but not in MOVES:")
        for f in unaccounted:
            print("  " + f)
        return 1

    # 1. Move first, so git records renames rather than delete+add.
    for old, new in MOVES.items():
        if old == new:
            continue
        dest = SRC / new
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "mv", str(SRC / old), str(dest)], cwd=REPO, check=True
        )
        print(f"  moved {old:44} -> {new}")

    # 2. Rewrite imports in every file, at its new location.
    for old, new in MOVES.items():
        path = SRC / new
        text = io.open(path, encoding="utf-8").read()
        updated = rewrite(text, old, new)
        if updated != text:
            io.open(path, "w", encoding="utf-8", newline="\n").write(updated)
            print(f"  imports rewritten in {new}")

    print("\nrestructure complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run it**

From the repository root, giving the full path to wherever you saved it:

```
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" "<scratchpad>\restructure.py"
```

Expected: 37 move lines, then import-rewrite lines, then
`restructure complete`. These numbers were verified against the tree as it
stands: 42 files, 37 of which move, and 51 relative imports rewritten. If your
counts differ, the tree has changed since this plan was written -- reconcile
`MOVES` before going further rather than after.

A `KeyError` naming a file and an import means `MOVES` is missing an entry.
Add it and rerun from a clean tree (`git checkout -- frontend/src`), never
patch around it.

- [ ] **Step 4: Run the frontend tests**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vitest\vitest.mjs run
```

Expected: `Test Files 14 passed (14)`, `Tests 69 passed (69)`. Any failure here
is a bad import path, not a behaviour change -- read the resolution error and
fix `MOVES`.

If you see `mockResolvedValue is not a function` rather than a resolution
error, a `vi.mock()` path was missed: the mock is registered against a module
nobody loads, so the component gets the real one. `VI_MOCK_RE` exists for
exactly this and the failure names the symptom, never the cause.

- [ ] **Step 5: Confirm git recorded renames, not rewrites**

```
git add -A
git status --short
```

Expected: lines beginning `R` (rename), not paired `D`/`A`. Renames keep
`git log --follow` and `git blame` working across the move, which matters for
files carrying the long design comments this codebase relies on.

- [ ] **Step 6: Run the full gate**

```
.\scripts\ccweb_check.cmd
```

Expected: `All checks passed.`

- [ ] **Step 7: Commit**

```
git commit -F <message-file>
```

Message subject: `Move the frontend into shared, store and owner trees`.
Body: state that this is a pure move, that no assertion in the 69 tests
changed, and that App.jsx/main.jsx/styles.css are split in later tasks.

---

### Task 2: Split the application shell in two

`App.jsx` becomes `StoreApp.jsx` and `OwnerApp.jsx`. The admin guard moves from
four per-route props to one wrapper at the console's router root.

**Files:**
- Create: `frontend/src/store/StoreApp.jsx`, `frontend/src/store/StoreApp.test.jsx`
- Create: `frontend/src/owner/OwnerApp.jsx`, `frontend/src/owner/OwnerApp.test.jsx`
- Create: `frontend/src/shared/LoginForm.jsx`, `frontend/src/shared/LoginForm.test.jsx`
- Create: `frontend/src/owner/pages/Login.jsx`
- Modify: `frontend/src/store/pages/Login.jsx` (becomes a thin wrapper)
- Modify: `frontend/src/main.jsx` (imports `./store/StoreApp` for now)
- Delete: `frontend/src/App.jsx`, `frontend/src/App.test.jsx`

**Interfaces:**
- Consumes: `../shared/auth-context` (`useAuth` returning
  `{ user, loading, isAdmin, login, register, logout }`), the moved page
  modules from Task 1.
- Produces: `StoreApp` (default export, no props), `OwnerApp` (default export,
  no props), `LoginForm` (default export, props
  `{ defaultRedirect: string, footer?: ReactNode }`).

- [ ] **Step 1: Write the failing tests for the console shell**

Create `frontend/src/owner/OwnerApp.test.jsx`:

```jsx
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

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
    expect(screen.getByRole('link', { name: /people/i })).toBeInTheDocument()
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
```

- [ ] **Step 2: Write the failing tests for the shop shell**

Create `frontend/src/store/StoreApp.test.jsx`. The first four cases are
`App.test.jsx` retargeted; the last two are new and are the point of this work.

```jsx
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import StoreApp from './StoreApp'
import { adminAuth, emptyCart, renderWithProviders } from '../test/helpers'

describe('shop shell', () => {
  it('renders the brand and the public navigation', () => {
    renderWithProviders(<StoreApp />)
    expect(screen.getByRole('link', { name: /ccwebdb/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /catalogue/i })).toBeInTheDocument()
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

  it('does not confirm that owner pages exist', () => {
    renderWithProviders(<StoreApp />, { auth: adminAuth(), route: '/admin/people' })
    expect(screen.getByText(/page not found/i)).toBeInTheDocument()
    expect(screen.queryByText(/administrator privileges/i)).not.toBeInTheDocument()
  })
})
```

The last case uses `adminAuth()` deliberately. Asserting a 404 for an
*anonymous* visitor would also pass if the route merely redirected to sign-in.
Using an administrator proves the route is absent rather than guarded.

- [ ] **Step 3: Run both to verify they fail**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vitest\vitest.mjs run src/owner/OwnerApp.test.jsx src/store/StoreApp.test.jsx
```

Expected: FAIL, `Failed to resolve import "./OwnerApp"` and `"./StoreApp"`.

- [ ] **Step 4: Extract the shared sign-in form**

Create `frontend/src/shared/LoginForm.jsx`. This is the body of the existing
`store/pages/Login.jsx` with the sign-up link lifted into a `footer` prop --
the console must not invite anyone to register.

```jsx
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from './auth-context'

/**
 * The sign-in form, shared by both applications.
 *
 * Only the surrounding chrome differs: the shop offers registration
 * underneath, the console offers nothing. `defaultRedirect` is where to land
 * when nothing sent the visitor here, which under the console's router
 * basename means the console's own root rather than the shop's.
 */
export default function LoginForm({ defaultRedirect = '/', footer = null }) {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(email, password)
      navigate(location.state?.from ?? defaultRedirect, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="narrow">
      <h1>Sign in</h1>
      <form onSubmit={submit}>
        <label>
          Email{/* */}
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Password{/* */}
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
      {footer}
    </section>
  )
}
```

- [ ] **Step 5: Replace the shop's Login page with a wrapper**

Replace the whole of `frontend/src/store/pages/Login.jsx`:

```jsx
import { Link } from 'react-router-dom'

import LoginForm from '../../shared/LoginForm'

export default function Login() {
  return (
    <LoginForm
      defaultRedirect="/"
      footer={
        <p className="muted small">
          No account? <Link to="/register">Register as a customer</Link>.
        </p>
      }
    />
  )
}
```

- [ ] **Step 6: Add the console's Login page**

Create `frontend/src/owner/pages/Login.jsx`. No footer: there is no console
account to self-register for.

```jsx
import LoginForm from '../../shared/LoginForm'

export default function Login() {
  return <LoginForm defaultRedirect="/" />
}
```

- [ ] **Step 7: Write StoreApp**

Create `frontend/src/store/StoreApp.jsx` -- the current `App.jsx` with every
admin link and admin route removed, and `RequireAuth`'s `adminOnly` branch
deleted along with them.

```jsx
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
```

- [ ] **Step 8: Write OwnerApp**

Create `frontend/src/owner/OwnerApp.jsx`.

```jsx
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'

import { useAuth } from '../shared/auth-context'
import AdminCoins from './pages/AdminCoins'
import AdminPeople from './pages/AdminPeople'
import { InventoryCoins, InventoryCurrency } from './pages/Inventory'
import Login from './pages/Login'

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
          <NavLink to="/manage/coins">Manage</NavLink>
          <NavLink to="/people">People</NavLink>
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
          <Route path="/manage/coins" element={<AdminCoins />} />
          <Route path="/people" element={<AdminPeople />} />
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
```

The console drops the `/admin/` URL prefix: under the `/owner` basename added
in Task 3, `/manage/coins` serves at `/owner/manage/coins`, and `/admin/admin`
would be silly.

- [ ] **Step 9: Point main.jsx at StoreApp and delete the old shell**

In `frontend/src/main.jsx`, change the import of `./App` to `./store/StoreApp`
and the element from `<App />` to `<StoreApp />`. Then:

```
git rm frontend/src/App.jsx frontend/src/App.test.jsx
```

`App.test.jsx` is deleted rather than kept because all four of its cases now
live in `StoreApp.test.jsx`; keeping both would assert the same behaviour twice
against a component that no longer exists.

- [ ] **Step 10: Write the LoginForm test**

Create `frontend/src/shared/LoginForm.test.jsx`:

```jsx
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import LoginForm from './LoginForm'
import { renderWithProviders } from '../test/helpers'

describe('LoginForm', () => {
  it('renders the fields both applications need', () => {
    renderWithProviders(<LoginForm defaultRedirect="/" />)
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows a footer only when one is supplied', () => {
    const { unmount } = renderWithProviders(<LoginForm defaultRedirect="/" />)
    expect(screen.queryByText(/register/i)).not.toBeInTheDocument()
    unmount()
    renderWithProviders(<LoginForm defaultRedirect="/" footer={<p>Register here</p>} />)
    expect(screen.getByText(/register here/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 11: Run the tests**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vitest\vitest.mjs run
```

Expected: **16 test files, 78 passing**. Files: 14 before, minus the deleted
`App.test.jsx`, plus `StoreApp.test.jsx`, `OwnerApp.test.jsx` and
`LoginForm.test.jsx`. Tests: 69 - 4 + 6 + 5 + 2 = 78.

`Login.test.jsx` must still pass unchanged -- if it does not, the wrapper
changed the shop's sign-in behaviour, which it must not.

If your counts differ from these, report the actual figures. Never adjust a
test to reach a predicted number: the prediction is arithmetic done in advance
and is the more likely thing to be wrong.

- [ ] **Step 12: Run the full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Split the application shell into shop and console`.

---

### Task 3: Give the console its own entry point

**Files:**
- Create: `frontend/owner.html`, `frontend/src/owner/main.jsx`
- Move: `frontend/src/main.jsx` -> `frontend/src/store/main.jsx`
- Modify: `frontend/index.html`, `frontend/vite.config.js`

**Interfaces:**
- Consumes: `StoreApp`, `OwnerApp` from Task 2.
- Produces: build inputs named `store` and `owner`, and
  `frontend/dist/.vite/manifest.json` -- Task 6 reads both names from it.

- [ ] **Step 1: Move the shop's entry module**

```
git mv frontend/src/main.jsx frontend/src/store/main.jsx
```

Then fix all five of its imports, because the file is now one level deeper:

| Before | After |
|---|---|
| `./store/StoreApp` | `./StoreApp` |
| `./shared/auth` | `../shared/auth` |
| `./store/cart` | `./cart` |
| `./shared/reference` | `../shared/reference` |
| `./styles.css` | `../styles.css` |

The stylesheet is the easy one to miss. It stays at `src/styles.css` until
Task 5, so from `src/store/main.jsx` it is `../styles.css`; leaving it as
`./styles.css` points at a file that does not exist yet and fails the build.

- [ ] **Step 2: Point index.html at the moved module**

In `frontend/index.html`, change the script `src` to `/src/store/main.jsx`.

- [ ] **Step 3: Create the console's entry module**

Create `frontend/src/owner/main.jsx`. Note there is no `CartProvider`: the
console has no cart, and leaving it out is what keeps `store/cart.jsx` out of
the console's graph.

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import OwnerApp from './OwnerApp'
import { AuthProvider } from '../shared/auth'
import { ReferenceProvider } from '../shared/reference'
import '../styles.css'

// basename, not a route prefix: every `to="/people"` in the console resolves
// under /owner, so no component needs to know where the console is mounted.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter basename="/owner">
      <AuthProvider>
        <ReferenceProvider>
          <OwnerApp />
        </ReferenceProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
```

- [ ] **Step 4: Create owner.html**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="robots" content="noindex, nofollow" />
    <title>ccwebdb console</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/owner/main.jsx"></script>
  </body>
</html>
```

`noindex, nofollow` keeps the console out of search results if it is ever
served publicly. It is a request to well-behaved crawlers, not a control --
the control is the deployment binding recorded under **Later** in the spec.

- [ ] **Step 5: Configure two inputs and the dev fallback**

Replace `frontend/vite.config.js` with:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two applications in one project: the shop and the owner console.
 *
 * Rollup builds each HTML entry as an independent module graph, which is what
 * keeps owner code out of the shop's bundle. `scripts/check-bundle-isolation.mjs`
 * asserts that against the built manifest rather than trusting it.
 */
function twoAppDevFallback() {
  return {
    name: 'ccwebdb-two-app-dev-fallback',
    // Returning a function post-hooks this middleware, so it runs after Vite's
    // own static and transform handling and only sees what would 404.
    configureServer(server) {
      return () => {
        server.middlewares.use((req, _res, next) => {
          const [path] = (req.url ?? '/').split('?')
          // Anything with an extension, or Vite's own internals, is a real
          // asset request and must not be rewritten to an HTML document.
          if (
            /\.[^/]+$/.test(path) ||
            path.startsWith('/@') ||
            path.startsWith('/src/') ||
            path.startsWith('/node_modules/')
          ) {
            return next()
          }
          req.url =
            path === '/owner' || path.startsWith('/owner/')
              ? '/owner.html'
              : '/index.html'
          next()
        })
      }
    },
  }
}

export default defineConfig({
  // 'mpa' switches off the single-entry SPA fallback, which would send
  // /owner/inventory/coins to the shop. twoAppDevFallback replaces it with one
  // that knows about both entries.
  appType: 'mpa',
  plugins: [react(), twoAppDevFallback()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Read by scripts/check-bundle-isolation.mjs.
    manifest: true,
    rollupOptions: {
      input: {
        store: 'index.html',
        owner: 'owner.html',
      },
    },
  },
  // Vitest reads this file, so the test run gets the same plugin and resolution
  // rules as the app rather than a second, drifting configuration.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    coverage: {
      provider: 'v8',
      // lcov is what SonarQube reads; text keeps the number visible in the
      // terminal so a drop is noticed before the scan runs.
      reporter: ['text-summary', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{js,jsx}'],
      // The entry modules only mount their app, and the test helpers are not
      // the subject.
      exclude: ['src/store/main.jsx', 'src/owner/main.jsx', 'src/test/**'],
    },
  },
})
```

- [ ] **Step 6: Verify both entries build**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vite\bin\vite.js build
```

Expected: output listing `dist/index.html` and `dist/owner.html`, and
`dist/.vite/manifest.json` present.

- [ ] **Step 7: Verify the dev fallback by hand**

Start the dev server, then check four URLs. This cannot be asserted in vitest
-- the middleware is server behaviour, not component behaviour -- so it is
checked once here and then held by the build check in Task 6.

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vite\bin\vite.js
```

In another window:

```
curl -s http://127.0.0.1:5173/ | findstr "store/main"
curl -s http://127.0.0.1:5173/cart | findstr "store/main"
curl -s http://127.0.0.1:5173/owner | findstr "owner/main"
curl -s http://127.0.0.1:5173/owner/inventory/coins | findstr "owner/main"
```

Expected: each prints the matching `<script src>` line. The third and fourth
are the ones that fail without the middleware, and the fourth is the one that
fails if the middleware matches `/owner` exactly rather than as a prefix.

- [ ] **Step 8: Run the full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Serve the console from its own entry point`.

---

### Task 4: Enforce the boundary in the lint gate

**Files:**
- Modify: `frontend/eslint.config.js`

**Interfaces:**
- Consumes: the tree layout from Task 1.
- Produces: nothing other tasks import.

- [ ] **Step 1: Add the boundary rules**

Append three blocks to the exported array in `frontend/eslint.config.js`,
**before** the trailing `prettier` entry:

```js
  // Import boundaries between the two applications.
  //
  // A static import by path is the only way owner code can enter the shop's
  // module graph, so this catches the cause at edit time. Written as glob
  // patterns rather than literal relative paths: a page nested one level
  // deeper reaches its sibling tree by '../../' rather than '../', and a
  // literal rule would silently stop matching exactly where a new page is
  // most likely to be added.
  //
  // src/test/ is deliberately unscoped. It is imported only by .test files,
  // which never enter a bundle.
  {
    files: ['src/store/**/*.{js,jsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/owner/**'],
              message:
                'The shop must not import owner code. Move what is shared into src/shared/.',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/owner/**/*.{js,jsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/store/**'],
              message:
                'The console must not import shop code. Move what is shared into src/shared/.',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/shared/**/*.{js,jsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/store/**', '**/owner/**'],
              message:
                'Shared code must not depend on either application; the dependency runs one way.',
            },
          ],
        },
      ],
    },
  },
```

- [ ] **Step 2: Run lint and expect it to pass immediately**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\eslint\bin\eslint.js .
```

Expected: no output, exit 0.

This step is an **audit of Task 1**, which is why the rules are added here
rather than first. A violation now means a module was filed in the wrong tree
during the move. Fix the placement, never the rule.

- [ ] **Step 3: Mutation-test the rules**

A rule that never fires is indistinguishable from a rule that does not work.
Temporarily add to `frontend/src/store/pages/Catalog.jsx`:

```js
import AdminPeople from '../../owner/pages/AdminPeople'
```

Run eslint. Expected: FAIL with `The shop must not import owner code.` Then
remove the line and confirm eslint passes again.

- [ ] **Step 4: Run the full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Enforce the shop/console import boundary in eslint`. Record in the
body that the rules were mutation-tested and what the deliberate violation was.

---

### Task 5: Split the stylesheet three ways

**Files:**
- Create: `frontend/src/shared/shared.css`, `frontend/src/owner/styles.css`
- Modify: `frontend/src/styles.css` -> moved to `frontend/src/store/styles.css`
- Modify: `frontend/src/store/main.jsx`, `frontend/src/owner/main.jsx`

**Interfaces:**
- Consumes: the entry modules from Task 3.
- Produces: nothing other tasks import.

- [ ] **Step 1: Move the stylesheet into the shop tree**

```
git mv frontend/src/styles.css frontend/src/store/styles.css
```

Update `frontend/src/store/main.jsx` to import `'./styles.css'`.

- [ ] **Step 2: Lift the owner-only rules out**

Create `frontend/src/owner/styles.css` and move these selector blocks into it
from `store/styles.css`, keeping their comments:

`.admin-form`, `.admin-form h2`, `.filter-grid`, `.filter-grid label`,
`.filter-grid select:disabled`, `.inventory-table`,
`.inventory-table th.sortable`, `.inventory-table th.sortable:hover`,
`.inventory-table td.wide`, `.review-mark`, `.bulk-bar`, `.review-pane`.

Check for others by searching `store/styles.css` for selectors used only by
files under `src/owner/`:

```
cd frontend\src
findstr /s /i /c:"className=" owner\*.jsx > %TEMP%\owner-classes.txt
```

Read the result and move any remaining owner-only selector.

- [ ] **Step 3: Lift the genuinely shared rules out**

Create `frontend/src/shared/shared.css` and move into it every rule used by
both trees: page scaffolding (`.app`, `.topbar`, `.brand`, `.brand-sub`,
`.nav`, `.account`, `.content`, `.footer`), typography and state
(`.muted`, `.small`, `.error`, `.badge`, `.narrow`), and form and button
rules used by `shared/LoginForm.jsx`.

- [ ] **Step 4: Import them in the right order**

In `frontend/src/store/main.jsx`:

```js
import '../shared/shared.css'
import './styles.css'
```

In `frontend/src/owner/main.jsx`, replace `import '../styles.css'` with:

```js
import '../shared/shared.css'
import './styles.css'
```

Shared first so an application's own rules win on equal specificity.

- [ ] **Step 5: Build and confirm the shop ships no owner class names**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vite\bin\vite.js build
findstr /c:"inventory-table" /c:"bulk-bar" /c:"review-pane" dist\assets\*.css
```

Expected: matches only in the console's stylesheet asset, never in the shop's.

A string search is the right tool **here** and the wrong tool for JavaScript:
Vite does not mangle CSS class names, so the string in the source is the string
in the output. Identifiers in JavaScript are minified, which is why Task 6
reads the module graph instead.

- [ ] **Step 6: Check both applications still render**

Start the dev server and load `/` and `/owner`. Confirm the shop's catalogue
and the console's inventory table are both styled. A missed rule shows as
unstyled content, not as an error.

- [ ] **Step 7: Run the full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Split the stylesheet so the shop ships no owner class names`.

---

### Task 6: Assert bundle isolation against the built artefact

The lint rules check the source. This checks the output, and the two fail for
different reasons: the lint rules catch a developer's import, this catches a
build-configuration mistake such as both entries sharing a chunk.

**Files:**
- Create: `frontend/scripts/check-bundle-isolation.mjs`
- Modify: `scripts/ccweb_check.cmd`
- Modify: `docs/code-quality.md`

**Interfaces:**
- Consumes: `frontend/dist/.vite/bundle-graph.json`, emitted by a build plugin
  added in Step 1 below. Entry chunks carry `name` `store` and `owner`.
- Produces: a script exiting 0 on success and 1 with a report on failure.

> **Why not Vite's `manifest.json`.** The earlier draft of this task read the
> manifest and asked whether any chunk's `src` named `src/owner/`. Inspecting a
> real build of this project shows why that cannot work: both entries import
> one shared chunk, recorded as
> `"_styles-DXfkAPNt.js": { "isEntry": false, "src": null }`. A manifest never
> lists the modules inside a chunk, and a shared chunk has no single `src` to
> name. Rollup puts any module imported by *both* entries into that shared
> chunk — so the day someone imports an owner page from shop code, the module
> lands in the chunk the shop already downloads, and a manifest-based check
> reports success. That is precisely the regression this task exists to catch,
> so the check reads chunk membership instead.

- [ ] **Step 1: Emit a bundle graph at build time, and pin the dev host**

Two changes to `frontend/vite.config.js`.

First, add `host: '127.0.0.1'` to the `server` block, above `port`:

```js
  server: {
    // Bind IPv4 loopback explicitly. Left unset, Vite binds only [::1] on this
    // machine, and every documented URL in docs/runtime-operations.md and in
    // this plan says 127.0.0.1 -- so the documented commands fail with
    // "connection refused" against a server that is running perfectly well.
    host: '127.0.0.1',
    port: 5173,
```

Second, add the plugin below — define it beside `twoAppDevFallback` and add
`bundleGraph()` to the `plugins` array:

```js
/**
 * Emit which modules ended up in which chunk.
 *
 * Vite's own manifest cannot answer this: it records a chunk's imports but not
 * its contents, and a chunk shared between entries has no `src` attributing it
 * to a source tree. Rollup only tells you inside `generateBundle`, so that is
 * where this listens.
 */
function bundleGraph() {
  return {
    name: 'ccwebdb-bundle-graph',
    generateBundle(_options, bundle) {
      const root = process.cwd().replace(/\\/g, '/')
      const chunks = {}
      for (const [fileName, chunk] of Object.entries(bundle)) {
        if (chunk.type !== 'chunk') continue
        chunks[fileName] = {
          name: chunk.name,
          isEntry: chunk.isEntry,
          imports: chunk.imports,
          dynamicImports: chunk.dynamicImports,
          modules: Object.keys(chunk.modules).map((id) =>
            id.replace(/\\/g, '/').replace(root + '/', ''),
          ),
        }
      }
      this.emitFile({
        type: 'asset',
        fileName: '.vite/bundle-graph.json',
        source: JSON.stringify(chunks, null, 2),
      })
    },
  }
}
```

- [ ] **Step 2: Write the check**

Create `frontend/scripts/check-bundle-isolation.mjs`:

```js
/**
 * Assert that neither application's bundle contains the other's code.
 *
 * Walks the chunk graph reachable from each entry and inspects the MODULES in
 * every chunk it reaches, not the chunk's name or `src`. Chunks shared between
 * the two entries are the whole point: Rollup puts a module imported by both
 * into one, it belongs to neither tree by name, and it is downloaded by both
 * applications. A check that cannot see inside it cannot see the failure it
 * exists to catch.
 *
 * Deliberately not a string search of the built JavaScript either: Rollup
 * minifies identifiers, so a grep can pass because the name it looked for was
 * renamed -- and a check that passes for the wrong reason is worse than none.
 *
 * Symmetric, unlike the one-directional check the spec describes. The lint
 * rules are symmetric and the extra direction costs nothing, so this closes
 * the case where console code reaches shop code by a route eslint cannot see.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const GRAPH = resolve(process.cwd(), 'dist/.vite/bundle-graph.json')

/**
 * Every chunk reachable from one entry, following imports transitively.
 *
 * Dynamic imports count. A lazily-loaded chunk is still the shop serving the
 * console's code to whoever asks, and dynamic import is the one form ESLint's
 * no-restricted-imports cannot see -- so this is the only layer that catches
 * it. Following static imports alone would leave both layers blind to the
 * same case.
 */
function reachable(chunks, entryFile) {
  const seen = new Set()
  const queue = [entryFile]
  while (queue.length > 0) {
    const file = queue.pop()
    if (seen.has(file)) continue
    seen.add(file)
    const chunk = chunks[file]
    for (const next of [...(chunk?.imports ?? []), ...(chunk?.dynamicImports ?? [])]) {
      queue.push(next)
    }
  }
  return seen
}

let chunks
try {
  chunks = JSON.parse(readFileSync(GRAPH, 'utf8'))
} catch {
  console.error(`Cannot read ${GRAPH}. Run the build first.`)
  process.exit(1)
}

const entries = Object.entries(chunks).filter(([, c]) => c.isEntry)
const find = (name) => entries.find(([, c]) => c.name === name)?.[0]

const store = find('store')
const owner = find('owner')

if (!store || !owner) {
  console.error('Could not find both entry chunks in the bundle graph.')
  console.error(`  entry chunks present: ${entries.map(([f, c]) => `${f} (name=${c.name})`).join(', ') || '(none)'}`)
  console.error('  expected chunks named "store" and "owner"')
  process.exit(1)
}

// Module ids are relative to the Vite root (frontend/), so they read
// 'src/owner/pages/AdminPeople.jsx' -- not 'frontend/src/...'. Verified
// against a real build; a prefix with 'frontend/' in it matches nothing and
// the check silently passes everything.
const failures = []
for (const [entryFile, label, forbidden] of [
  [store, 'shop', 'src/owner/'],
  [owner, 'console', 'src/store/'],
]) {
  for (const file of reachable(chunks, entryFile)) {
    for (const module of chunks[file]?.modules ?? []) {
      if (module.includes(forbidden)) {
        failures.push(`${label} bundle reaches ${module} (in chunk ${file})`)
      }
    }
  }
}

if (failures.length > 0) {
  console.error('Bundle isolation FAILED:')
  for (const f of failures) console.error('  ' + f)
  process.exit(1)
}

const counted = new Set([...reachable(chunks, store), ...reachable(chunks, owner)])
console.log(
  `Bundle isolation OK: neither entry reaches the other tree (${counted.size} chunks inspected).`,
)
```

- [ ] **Step 3: Run it against a clean build**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vite\bin\vite.js build
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" scripts\check-bundle-isolation.mjs
```

Expected: `Bundle isolation OK: neither entry reaches the other tree.`

If instead it reports `Could not find both entries in the manifest`, read the
entry keys it printed and adjust `findEntry`'s expected paths. Do **not**
loosen the check to make it pass -- a check that cannot locate what it is
guarding is guarding nothing.

- [ ] **Step 4: Mutation-test it**

The check must be able to fail. Temporarily add to
`frontend/src/store/StoreApp.jsx`:

```js
import AdminPeople from '../owner/pages/AdminPeople'
console.warn(AdminPeople)
```

(The `console.warn` matters -- without a use, Rollup tree-shakes the import
away and the mutation tests nothing.)

Rebuild and rerun. Expected:

```
Bundle isolation FAILED:
  shop bundle reaches src/owner/pages/AdminPeople.jsx (in chunk assets/styles-<hash>.js)
```

and exit 1. Remove the lines, rebuild, and confirm it passes again.

**Read that expected output carefully — the chunk named is the *shared* one,
not the shop's own.** This was verified against a real build of this project
before the task was written: a module imported by both entries is hoisted by
Rollup into the chunk they share, which the manifest records as
`"src": null`, belonging to no tree by name. The shop still downloads it. That
is exactly why the check reads chunk membership rather than the manifest, and
exactly the case an implementation that "simplified" it back to the manifest
would wave through.

If it does **not** fail, the check is decorative and must be fixed before this
task is complete. Print the bundle graph and find which chunk the module
actually landed in.

- [ ] **Step 5: Wire it into the gate**

In `scripts\ccweb_check.cmd`, inside the existing
`if exist "frontend\node_modules\eslint" (` block and after the frontend tests,
add:

```
    echo === frontend bundle isolation ===
    rem  Builds both entries and asserts neither reaches the other's tree.
    rem  The eslint boundary rules check the source; this checks the artefact,
    rem  so a build-configuration mistake cannot pass unnoticed.
    pushd frontend
    "%NODE%" node_modules\vite\bin\vite.js build
    if errorlevel 1 set "FAILED=!FAILED! build"
    "%NODE%" scripts\check-bundle-isolation.mjs
    if errorlevel 1 set "FAILED=!FAILED! isolation"
    popd
```

Edit the file byte-wise to preserve its CRLF line endings, which
`.gitattributes` pins for `*.cmd`. Verify afterwards:

```
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -c "b=open('scripts/ccweb_check.cmd','rb').read(); print('bare LF:', b.count(b'\n')-b.count(b'\r\n'))"
```

Expected: `bare LF: 0`.

- [ ] **Step 6: Confirm the gate fails and recovers**

Reapply the Step 4 mutation, run `.\scripts\ccweb_check.cmd`, and expect a
non-zero exit with `FAILED:` naming `isolation` (and `eslint`, since the lint
rules catch the same import). Remove it and expect `All checks passed.`

- [ ] **Step 7: Document it**

In `docs/code-quality.md`, add `Frontend bundle isolation | custom | yes` to
the **What runs** table, and a short section explaining the two-channel check
and why the JavaScript side reads the manifest rather than grepping.

- [ ] **Step 8: Run the full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Assert bundle isolation against real chunk membership`. Record the
mutation test and its result in the body.

---

## Done when

- `.\scripts\ccweb_check.cmd` exits 0, including the new isolation gate.
- The shop returns "Page not found" for `/admin/people` **to an administrator**,
  proving the route is absent rather than guarded.
- `frontend/dist/assets/*.css` for the shop contains none of
  `inventory-table`, `bulk-bar`, `review-pane`.
- Both the eslint boundary rules and the manifest check have been shown to fail
  on a deliberate violation and to pass once it is removed.
- Neither application links to the other.

## Deliberate deviations from the spec

**The manifest check is symmetric.** The spec describes walking the shop
entry's graph only. The lint rules are already symmetric, the second direction
costs nothing, and it closes console-reaches-shop, which eslint alone would
miss if a module were mis-filed. A strict superset of what was approved.

**Sign-in sits outside the console's root guard.** The spec says the guard
wraps the whole router; a guard covering its own sign-in page would lock
everyone out. Every other route is inside it.

**The console drops the `/admin/` URL prefix.** Under the `/owner` basename,
`AdminCoins` serves at `/owner/manage/coins` rather than `/owner/admin/coins`.

## Not in this plan

The receiving page is specified separately and lands in
`src/owner/pages/Receiving.jsx` once this structure exists. Production
serving, and binding the console where the public cannot reach it, are
recorded under **Later** in the spec and need a deployment story that does not
exist yet.
