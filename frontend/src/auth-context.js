import { createContext, useContext } from 'react'

/**
 * The auth context and the hook that reads it.
 *
 * Separate from the provider because a module that exports a component *and*
 * something else breaks React Fast Refresh: the bundler can no longer tell
 * whether a change to the file is a component edit it can hot-swap, so it
 * falls back to a full reload and loses application state.
 *
 * Contexts and hooks are not components, so they live here and the provider
 * lives in auth.jsx.
 */
export const AuthContext = createContext(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside an AuthProvider')
  return ctx
}
