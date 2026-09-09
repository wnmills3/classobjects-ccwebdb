import { useCallback, useEffect, useMemo, useState } from 'react'

import { api, clearTokens, loadTokens, saveTokens } from './api'
import { AuthContext } from './auth-context'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // On first mount, exchange any stored token for the current profile. This
  // also silently drops tokens that expired while the tab was closed.
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
      } catch {
        clearTokens()
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
      isAdmin: user?.role === 'admin',
    }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
