import { act, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  api: { login: vi.fn(), me: vi.fn(), register: vi.fn() },
  loadTokens: vi.fn(),
  saveTokens: vi.fn(),
  clearTokens: vi.fn(),
}))

import { api, clearTokens, loadTokens, saveTokens } from './api'
import { AuthProvider } from './auth'
import { useAuth } from './auth-context'

const ADMIN = { id: 1, email: 'admin@example.com', role: 'admin' }
const BUYER = { id: 2, email: 'buyer@example.com', role: 'customer' }

let auth

function Probe() {
  const value = useAuth()
  // Captured in an effect, not during render: reassigning a module
  // variable while rendering is a side effect React's rules forbid.
  useEffect(() => {
    auth = value
  })
  return (
    <div>
      <span data-testid="user">{value.user?.email ?? 'anonymous'}</span>
      <span data-testid="loading">{String(value.loading)}</span>
      <span data-testid="admin">{String(value.isAdmin)}</span>
    </div>
  )
}

const mount = () =>
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )

beforeEach(() => {
  vi.clearAllMocks()
  auth = undefined
})

describe('AuthProvider', () => {
  it('finishes loading as anonymous when no token is stored', async () => {
    loadTokens.mockReturnValue(null)
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('loading')).toHaveTextContent('false'),
    )
    expect(screen.getByTestId('user')).toHaveTextContent('anonymous')
    expect(api.me).not.toHaveBeenCalled()
  })

  it('restores the signed-in user from a stored token', async () => {
    loadTokens.mockReturnValue({ access_token: 'a', refresh_token: 'r' })
    api.me.mockResolvedValue(ADMIN)
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('user')).toHaveTextContent('admin@example.com'),
    )
    expect(screen.getByTestId('admin')).toHaveTextContent('true')
  })

  it('drops a token the server no longer accepts', async () => {
    // Expiring while the tab was closed must leave the app anonymous and
    // usable, not stuck on a token that will fail every later request.
    loadTokens.mockReturnValue({ access_token: 'stale' })
    api.me.mockRejectedValue(new Error('401'))
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('loading')).toHaveTextContent('false'),
    )
    expect(clearTokens).toHaveBeenCalled()
    expect(screen.getByTestId('user')).toHaveTextContent('anonymous')
  })

  it('stores the tokens a login returns and then loads the profile', async () => {
    loadTokens.mockReturnValue(null)
    api.login.mockResolvedValue({ access_token: 'new', refresh_token: 'r' })
    api.me.mockResolvedValue(BUYER)
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('loading')).toHaveTextContent('false'),
    )

    await act(() => auth.login('buyer@example.com', 'hunter2'))

    expect(api.login).toHaveBeenCalledWith('buyer@example.com', 'hunter2')
    expect(saveTokens).toHaveBeenCalledWith({ access_token: 'new', refresh_token: 'r' })
    expect(screen.getByTestId('user')).toHaveTextContent('buyer@example.com')
    expect(screen.getByTestId('admin')).toHaveTextContent('false')
  })

  it('signs in straight after registering', async () => {
    loadTokens.mockReturnValue(null)
    api.register.mockResolvedValue(BUYER)
    api.login.mockResolvedValue({ access_token: 'new' })
    api.me.mockResolvedValue(BUYER)
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('loading')).toHaveTextContent('false'),
    )

    await act(() =>
      auth.register({ email: 'buyer@example.com', password: 'p', full_name: 'B' }),
    )
    expect(api.login).toHaveBeenCalledWith('buyer@example.com', 'p')
    expect(screen.getByTestId('user')).toHaveTextContent('buyer@example.com')
  })

  it('clears the stored tokens on logout', async () => {
    loadTokens.mockReturnValue({ access_token: 'a' })
    api.me.mockResolvedValue(ADMIN)
    mount()
    await waitFor(() =>
      expect(screen.getByTestId('user')).toHaveTextContent('admin@example.com'),
    )

    act(() => auth.logout())
    expect(clearTokens).toHaveBeenCalled()
    expect(screen.getByTestId('user')).toHaveTextContent('anonymous')
  })
})
