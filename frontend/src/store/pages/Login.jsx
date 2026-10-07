import { Link } from 'react-router-dom'

import LoginForm from '../../shared/LoginForm'

/**
 * The shop's sign-in page: the shared form, with a link beneath it to
 * register for those with no account.
 */
export default function Login() {
  return (
    <LoginForm
      defaultRedirect="/"
      footer={
        <p className="muted small">
          No account? <Link to="/register">Register as a customer</Link>.
        </p>
      }
    />
  )
}
