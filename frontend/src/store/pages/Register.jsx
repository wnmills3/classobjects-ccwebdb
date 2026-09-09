import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../../shared/auth-context'

export default function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ email: '', password: '', full_name: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function update(field) {
    return (event) => setForm((f) => ({ ...f, [field]: event.target.value }))
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await register(form)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="narrow">
      <h1>Create an account</h1>
      <form onSubmit={submit}>
        <label>
          Full name{/* */}
          <input value={form.full_name} onChange={update('full_name')} />
        </label>
        <label>
          Email{/* */}
          <input
            type="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={update('email')}
          />
        </label>
        <label>
          Password{/* */}
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={form.password}
            onChange={update('password')}
          />
          <span className="muted small">At least 8 characters.</span>
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Creating...' : 'Create account'}
        </button>
      </form>
      <p className="muted small">
        Already registered? <Link to="/login">Sign in</Link>.
      </p>
    </section>
  )
}
