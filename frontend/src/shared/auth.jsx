import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, api, clearTokens, loadTokens, saveTokens } from './api'
import { AuthContext } from './auth-context'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // On first mount, exchange any stored token for the current profile. This
  // also drops tokens that expired while the tab was closed -- but only when
  // the server refuses them (401). A server that is down or a dropped
  // connection says nothing about the tokens, so they are kept for the next
  // load rather than signing the visitor out over a blip.
  useEffect(() => {
    let cancelled = false
    async function bootstrap() {
      if (!loadTokens()) {
        setLoading(false)
        return
      }
      try {
        const me = await api.me()
        if (!cancelled) setUser(me)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) clearTokens()
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    bootstrap()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email, password) => {
    const tokens = await api.login(email, password)
    saveTokens(tokens)
    const me = await api.me()
    setUser(me)
    return me
  }, [])

  const register = useCallback(
    async (payload) => {
      await api.register(payload)
      return login(payload.email, payload.password)
    },
    [login],
  )

  const logout = useCallback(() => {
    clearTokens()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({
      user,
      loading,
      login,
      register,
      logout,
      isAdmin: user?.role === 'manager',
    }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
