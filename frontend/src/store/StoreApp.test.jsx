import userEvent from '@testing-library/user-event'
import { act, screen } from '@testing-library/react'
import { useMemo, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'

const SEAL_SCRIPT = 'https://seal.digicert.com/seals/cascade/seal.min.js'

vi.mock('../shared/api', () => ({
  api: {
    listCatalog: vi
      .fn()
      .mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 }),
    listMyOrders: vi.fn().mockResolvedValue([]),
    getShop: vi.fn().mockResolvedValue({ open: true }),
  },
}))

import StoreApp from './StoreApp'
import { api } from '../shared/api'
import { AuthContext } from '../shared/auth-context'
import { SHOP_NAME } from '../shared/site'
import {
  adminAuth,
  anonymousAuth,
  customerAuth,
  emptyCart,
  renderWithProviders,
} from '../test/helpers'

/** An auth context whose login signs a customer in, as the real one does. */
function SignsIn({ children }) {
  const [user, setUser] = useState(null)
  const value = useMemo(
    () => anonymousAuth({ user, login: async () => setUser(customerAuth().user) }),
    [user],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/**
 * Render the shop and wait until it has asked whether the shop is open --
 * until then it shows only "Loading...", and an assertion that something is
 * absent would pass against that.
 */
async function renderShop(ui, options) {
  const result = renderWithProviders(ui, options)
  await screen.findByRole('contentinfo')
  return result
}

/**
 * Run the effects React has scheduled but not yet run.
 *
 * The footer appears once the mocked shop request resolves, outside act, so
 * findByRole can return before the seal's useEffect has run. Asserting on
 * what that effect does -- or that it did nothing -- needs it run first.
 */
async function settleEffects() {
  await act(async () => {})
}

describe('shop shell', () => {
  it('renders the brand and the public navigation', async () => {
    await renderShop(<StoreApp />)
    expect(
      screen.getByRole('link', { name: new RegExp(`^${SHOP_NAME}`) }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/ccwebdb/i)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /catalog/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^cart$/i })).toBeInTheDocument()
  })

  it('hides Orders from an anonymous visitor', async () => {
    await renderShop(<StoreApp />)
    expect(screen.queryByRole('link', { name: /orders/i })).not.toBeInTheDocument()
  })

  it('shows Orders once someone is signed in', async () => {
    await renderShop(<StoreApp />, { auth: adminAuth() })
    expect(screen.getByRole('link', { name: /orders/i })).toBeInTheDocument()
  })

  it('puts the item count in the cart link only when the cart has something in it', async () => {
    await renderShop(<StoreApp />, { cart: emptyCart({ count: 3 }) })
    expect(screen.getByRole('link', { name: /cart \(3\)/i })).toBeInTheDocument()
  })

  it('says nothing of a development build in the footer', async () => {
    await renderShop(<StoreApp />)
    const footer = screen.getByRole('contentinfo')
    expect(footer).not.toHaveTextContent(/sample data|development build/i)
  })

  it('shows no seal and loads no script when no seal is configured', async () => {
    // A script left by another test would make "none loaded" meaningless.
    document.querySelectorAll(`script[src="${SEAL_SCRIPT}"]`).forEach((s) => s.remove())
    vi.stubEnv('VITE_DIGICERT_SEAL_ID', '')
    try {
      await renderShop(<StoreApp />)
      await settleEffects()
      const footer = screen.getByRole('contentinfo')
      expect(footer.querySelector('[id^="DigiCertClickID_"]')).toBeNull()
      expect(document.querySelectorAll(`script[src="${SEAL_SCRIPT}"]`)).toHaveLength(0)
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('puts the certificate seal in the footer and loads its script once', async () => {
    vi.stubEnv('VITE_DIGICERT_SEAL_ID', 'kOj5Uc3j')
    onTestFinished(() => vi.unstubAllEnvs())
    // StrictMode mounts the shell twice; a second script would draw a second seal.
    await renderShop(<StoreApp />, { strict: true })
    await settleEffects()
    const footer = screen.getByRole('contentinfo')
    expect(footer.querySelector('#DigiCertClickID_kOj5Uc3j')).not.toBeNull()
    const scripts = document.querySelectorAll(`script[src="${SEAL_SCRIPT}"]`)
    expect(scripts).toHaveLength(1)
    expect(window.__dcid).toContainEqual({
      cid: 'DigiCertClickID_kOj5Uc3j',
      tag: 'kOj5Uc3j',
    })
  })

  it('offers no console navigation, even to an administrator', async () => {
    await renderShop(<StoreApp />, { auth: adminAuth() })
    expect(screen.queryByRole('link', { name: /manage/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /people/i })).not.toBeInTheDocument()
  })

  it('returns to the page that asked for sign-in', async () => {
    // A guard that sends a visitor to /login without saying where from lands
    // them on the catalog after signing in from a bookmarked /orders.
    const user = userEvent.setup()
    await renderShop(
      <SignsIn>
        <StoreApp />
      </SignsIn>,
      { route: '/orders' },
    )

    // The redirect to sign-in lands a render after the shell itself does.
    await user.type(await screen.findByLabelText(/email/i), 'buyer@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByRole('heading', { name: 'Your orders' }),
    ).toBeInTheDocument()
  })

  // Both console paths the spec names, not just one: a route left in the
  // shop would be found by whichever URL nobody thought to assert.
  // adminAuth() deliberately -- for an anonymous visitor a redirect to sign-in
  // would also satisfy "not found", so only an administrator seeing a 404
  // proves the route is absent rather than merely guarded.
  it.each(['/people', '/inventory/coins'])(
    'does not confirm that %s exists',
    async (route) => {
      await renderShop(<StoreApp />, { auth: adminAuth(), route })
      expect(screen.getByText(/page not found/i)).toBeInTheDocument()
      expect(screen.queryByText(/privileges/i)).not.toBeInTheDocument()
    },
  )
})

describe('shop before opening day', () => {
  beforeEach(() => {
    api.getShop.mockResolvedValue({ open: false })
    // Earlier tests asked the catalog; nothing here may.
    api.listCatalog.mockClear()
  })
  afterEach(() => {
    api.getShop.mockResolvedValue({ open: true })
  })

  it.each(['/', '/cart', '/register', '/coins/1'])(
    'shows a signed-out visitor only the coming-soon page at %s',
    async (route) => {
      await renderShop(<StoreApp />, { route })
      expect(screen.getByRole('heading', { name: 'Coming soon' })).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /catalog/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /cart/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /register/i })).not.toBeInTheDocument()
      expect(api.listCatalog).not.toHaveBeenCalled()
    },
  )

  it('still lets a visitor sign in, with no offer to register', async () => {
    await renderShop(<StoreApp />, { route: '/login' })
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByText(/no account/i)).not.toBeInTheDocument()
  })

  it('shows a signed-in account the shop as usual', async () => {
    await renderShop(<StoreApp />, { auth: customerAuth() })
    expect(screen.getByRole('link', { name: /catalog/i })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Coming soon' }),
    ).not.toBeInTheDocument()
  })
})

describe("the shop's name and owner", () => {
  /**
   * Render the shop as built with `env` as its settings. The name is read
   * when the module loads, so the shop and everything it shares a context
   * with are loaded afresh under the stubbed settings.
   */
  async function renderBuiltWith(env, { open = true } = {}) {
    for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value)
    onTestFinished(() => vi.unstubAllEnvs())
    vi.resetModules()
    const { default: App } = await import('./StoreApp')
    const helpers = await import('../test/helpers')
    // The fresh shop asks a fresh mock, not the one imported above.
    const fresh = await import('../shared/api')
    fresh.api.getShop.mockResolvedValue({ open })
    helpers.renderWithProviders(<App />)
    return screen.findByRole('contentinfo')
  }

  it('comes from the settings', async () => {
    const footer = await renderBuiltWith({
      VITE_SHOP_NAME: 'Example Mint',
      VITE_SHOP_OWNER: 'Example Mint LLC',
    })
    expect(screen.getByRole('link', { name: /^Example Mint/ })).toBeInTheDocument()
    expect(footer).toHaveTextContent(`© ${new Date().getFullYear()} Example Mint LLC`)
  })

  it('is a neutral name, with no copyright holder, when nothing is set', async () => {
    const footer = await renderBuiltWith({ VITE_SHOP_NAME: '', VITE_SHOP_OWNER: '' })
    expect(screen.getByRole('link', { name: /^Coin Shop/ })).toBeInTheDocument()
    expect(footer).toHaveTextContent(/^Coin Shop$/)
  })

  it('names the shop on the coming-soon page', async () => {
    await renderBuiltWith({ VITE_SHOP_NAME: 'Example Mint' }, { open: false })
    expect(screen.getByText(/^Example Mint is getting ready/)).toBeInTheDocument()
  })
})
