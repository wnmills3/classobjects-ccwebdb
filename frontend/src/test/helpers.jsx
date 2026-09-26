/**
 * Render helpers for component tests.
 *
 * Every page reads at least one context and most use the router, so rendering
 * one bare throws before it can be asserted on. These wrap a component in the
 * providers it expects, with overridable defaults, so a test states only the
 * part it cares about.
 */
import { StrictMode } from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthContext } from '../shared/auth-context'
import { CartContext } from '../store/cart-context'
import { ReferenceContext } from '../shared/reference-context'

export function anonymousAuth(overrides = {}) {
  return {
    user: null,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    isAdmin: false,
    ...overrides,
  }
}

export function adminAuth(overrides = {}) {
  return anonymousAuth({
    user: { id: 1, email: 'admin@example.com', role: 'manager', full_name: 'Manager' },
    isAdmin: true,
    ...overrides,
  })
}

export function customerAuth(overrides = {}) {
  return anonymousAuth({
    user: { id: 2, email: 'buyer@example.com', role: 'customer', full_name: 'Buyer' },
    ...overrides,
  })
}

export function emptyCart(overrides = {}) {
  return {
    lines: [],
    add: vi.fn(),
    setQuantity: vi.fn(),
    remove: vi.fn(),
    clear: vi.fn(),
    count: 0,
    // A decimal string, as the real cart's total is.
    total: '0.00',
    ...overrides,
  }
}

/**
 * The table row whose accessible name starts with `prefix` -- the text of its
 * first cell. `prefix` is matched literally, so a code like `CC-000001` or a
 * name with a `+` in it needs no escaping by the caller.
 */
export function rowNamed(prefix) {
  const literal = prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return screen.getByRole('row', { name: new RegExp(`^${literal}`) })
}

export function emptyReference(overrides = {}) {
  return { tables: {}, load: vi.fn(), invalidate: vi.fn(), ...overrides }
}

export function renderWithProviders(ui, options = {}) {
  const {
    auth = anonymousAuth(),
    cart = emptyCart(),
    reference = emptyReference(),
    route = '/',
    // Only management-console tests that specifically care about the console's
    // real mount point (`basename="/management"` in `management/main.jsx`) need this --
    // e.g. telling a `<Link>`'s routed href apart from a hard-coded path
    // that happens to read the same without a basename in play.
    basename,
    // Renders inside `<StrictMode>`, which is how both applications run in
    // development (each `main.jsx`): React then invokes every effect setup,
    // cleanup, setup on mount. A guard that is armed in a setup and disarmed
    // in its cleanup without being re-armed is left disarmed for the
    // component's whole life -- a class of bug no ordinary render can see, so
    // at least one test per such component asks for this.
    strict = false,
    ...rest
  } = options

  const tree = (
    <MemoryRouter initialEntries={[route]} basename={basename}>
      <AuthContext.Provider value={auth}>
        <CartContext.Provider value={cart}>
          <ReferenceContext.Provider value={reference}>{ui}</ReferenceContext.Provider>
        </CartContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  )

  return render(strict ? <StrictMode>{tree}</StrictMode> : tree, rest)
}
