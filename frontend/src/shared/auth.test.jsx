import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', async (importOriginal) => ({
  ApiError: (await importOriginal()).ApiError,
  api: { login: vi.fn(), me: vi.fn(), register: vi.fn() },
  loadTokens: vi.fn(),
  saveTokens: vi.fn(),
  clearTokens: vi.fn(),
  onSignedOut: vi.fn(() => () => {}),
}))

import { ApiError, api, clearTokens, loadTokens, onSignedOut, saveTokens } from './api'
import { AuthProvider } from './auth'
import { useAuth } from './auth-context'
import { adminAuth, customerAuth } from '../test/helpers'

//: The profiles `GET /api/auth/me` answers with.
const ADMIN = adminAuth().user
const BUYER = customerAuth().user

/** The auth context as a component sees it; `result.current` is the latest. */
async function mount() {
  const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
  await waitFor(() => expect(result.current.loading).toBe(false))
  return result
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AuthProvider', () => {
  it('finishes loading as anonymous when no token is stored', async () => {
    loadTokens.mockReturnValue(null)
    const auth = await mount()
    expect(auth.current.user).toBeNull()
    expect(api.me).not.toHaveBeenCalled()
  })

  it('restores the signed-in user from a stored token', async () => {
    loadTokens.mockReturnValue({ access_token: 'a', refresh_token: 'r' })
    api.me.mockResolvedValue(ADMIN)
    const auth = await mount()
    expect(auth.current.user).toEqual(ADMIN)
    expect(auth.current.isAdmin).toBe(true)
  })

  it('drops a token the server no longer accepts', async () => {
    // Expiring while the tab was closed must leave the app anonymous and
    // usable, not stuck on a token that will fail every later request.
    loadTokens.mockReturnValue({ access_token: 'stale' })
    api.me.mockRejectedValue(new ApiError(401, 'Could not validate credentials'))
    const auth = await mount()
    expect(clearTokens).toHaveBeenCalled()
    expect(auth.current.user).toBeNull()
  })

  it.each([
    ['a server that is down', new ApiError(503, 'Service Unavailable')],
    ['no network at all', new TypeError('Failed to fetch')],
  ])('keeps the stored tokens through %s', async (_case, failure) => {
    // Only the server saying no (401) means the tokens are no good. A restart
    // of the API or a dropped connection says nothing about them, and
    // clearing them would sign the owner out of every tab over a blip.
    loadTokens.mockReturnValue({ access_token: 'a', refresh_token: 'r' })
    api.me.mockRejectedValue(failure)
    await mount()
    expect(clearTokens).not.toHaveBeenCalled()
  })

  it('stores the tokens a login returns and then loads the profile', async () => {
    loadTokens.mockReturnValue(null)
    api.login.mockResolvedValue({ access_token: 'new', refresh_token: 'r' })
    api.me.mockResolvedValue(BUYER)
    const auth = await mount()

    await act(() => auth.current.login('buyer@example.com', 'hunter2'))

    expect(api.login).toHaveBeenCalledWith('buyer@example.com', 'hunter2')
    expect(saveTokens).toHaveBeenCalledWith({ access_token: 'new', refresh_token: 'r' })
    expect(auth.current.user).toEqual(BUYER)
    expect(auth.current.isAdmin).toBe(false)
  })

  it('signs in straight after registering', async () => {
    loadTokens.mockReturnValue(null)
    api.register.mockResolvedValue(BUYER)
    api.login.mockResolvedValue({ access_token: 'new' })
    api.me.mockResolvedValue(BUYER)
    const auth = await mount()

    await act(() =>
      auth.current.register({
        email: 'buyer@example.com',
        password: 'p',
        full_name: 'B',
      }),
    )
    expect(api.login).toHaveBeenCalledWith('buyer@example.com', 'p')
    expect(auth.current.user).toEqual(BUYER)
  })

  it('shows nobody signed in once the session is refused mid-visit', async () => {
    // The tokens expire, or the password is reset, while the page is open.
    // A header still naming the account beside requests that all fail gives
    // no way to the sign-in form short of a reload.
    loadTokens.mockReturnValue({ access_token: 'a', refresh_token: 'r' })
    api.me.mockResolvedValue(BUYER)
    const auth = await mount()
    expect(auth.current.user).toEqual(BUYER)

    const signedOut = onSignedOut.mock.calls.at(-1)[0]
    act(() => signedOut())

    expect(auth.current.user).toBeNull()
  })

  it('stops listening for a refused session when it unmounts', async () => {
    const stop = vi.fn()
    onSignedOut.mockReturnValueOnce(stop)
    loadTokens.mockReturnValue(null)
    const { result, unmount } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.loading).toBe(false))

    unmount()

    expect(stop).toHaveBeenCalledTimes(1)
  })

  it('clears the stored tokens on logout', async () => {
    loadTokens.mockReturnValue({ access_token: 'a' })
    api.me.mockResolvedValue(ADMIN)
    const auth = await mount()
    expect(auth.current.user).toEqual(ADMIN)

    act(() => auth.current.logout())
    expect(clearTokens).toHaveBeenCalled()
    expect(auth.current.user).toBeNull()
  })
})
