import { Link } from 'react-router-dom'

import LoginForm from '../../shared/LoginForm'

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
