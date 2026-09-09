/**
 * Render helpers for component tests.
 *
 * Every page reads at least one context and most use the router, so rendering
 * one bare throws before it can be asserted on. These wrap a component in the
 * providers it expects, with overridable defaults, so a test states only the
 * part it cares about.
 */
import { render } from '@testing-library/react'
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
    user: { id: 1, email: 'admin@example.com', role: 'admin', full_name: 'Admin' },
    isAdmin: true,
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
    total: 0,
    ...overrides,
  }
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
    ...rest
  } = options

  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthContext.Provider value={auth}>
        <CartContext.Provider value={cart}>
          <ReferenceContext.Provider value={reference}>{ui}</ReferenceContext.Provider>
        </CartContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
    rest,
  )
}
