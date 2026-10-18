import { Link } from 'react-router-dom'

import LoginForm from '../../shared/LoginForm'

/**
 * The shop's sign-in page: the shared form, with a link beneath it to
 * register for those with no account -- left out while the shop is closed,
 * when registration is refused.
 */
export default function Login({ canRegister = true }) {
  return (
    <LoginForm
      defaultRedirect="/"
      footer={
        canRegister && (
          <p className="muted small">
            No account? <Link to="/register">Register as a customer</Link>.
          </p>
        )
      }
    />
  )
}
