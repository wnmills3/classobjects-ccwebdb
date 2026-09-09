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
