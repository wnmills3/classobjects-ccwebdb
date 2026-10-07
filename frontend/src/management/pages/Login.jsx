import LoginForm from '../../shared/LoginForm'

/**
 * The console's sign-in page: the shared form, which goes on to the
 * console's first page unless another was asked for.
 */
export default function Login() {
  return <LoginForm defaultRedirect="/" />
}
