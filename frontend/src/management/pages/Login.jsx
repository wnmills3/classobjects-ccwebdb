import LoginForm from '../../shared/LoginForm'
import { SHOP_NAME } from '../../shared/site'

/**
 * The console's sign-in page: the shared form, which goes on to the
 * console's first page unless another was asked for.
 *
 * It is routed outside the console shell, which only a signed-in
 * administrator gets, so it carries the shell's frame itself: without
 * `.app` the form sits against the window's edge.
 */
export default function Login() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          {SHOP_NAME}
          <span className="brand-sub">Console</span>
        </span>
      </header>
      <main className="content">
        <LoginForm defaultRedirect="/" />
      </main>
    </div>
  )
}
