import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../../shared/api'
import { useAuth } from '../../shared/auth-context'
import { useCart } from '../cart-context'
import { money } from '../../shared/format'

export default function Cart() {
  const { lines, setQuantity, remove, clear, total } = useCart()
  const { user } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [placed, setPlaced] = useState(null)
  const [busy, setBusy] = useState(false)

  async function checkout() {
    setBusy(true)
    setError('')
    try {
      const order = await api.createOrder(
        lines.map((l) => ({ listing_id: l.coin.id, quantity: l.quantity })),
      )
      clear()
      setPlaced(order)
    } catch (err) {
      // A 409 here means stock ran out between browsing and checkout.
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (placed) {
    return (
      <section className="narrow">
        <h1>Thank you</h1>
        <p>
          Order #{placed.id} was placed for {money(placed.total_amount)}.
        </p>
        <p>
          <Link to="/orders">View your orders</Link> or{' '}
          <Link to="/">continue browsing</Link>.
        </p>
      </section>
    )
  }

  if (lines.length === 0) {
    return (
      <section>
        <h1>Cart</h1>
        <p className="muted">
          Your cart is empty. <Link to="/">Browse the catalogue</Link>.
        </p>
      </section>
    )
  }

  return (
    <section>
      <h1>Cart</h1>
      <table className="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Unit price</th>
            <th>Quantity</th>
            <th>Line total</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lines.map(({ coin, quantity }) => (
            <tr key={coin.id}>
              <td>
                <Link to={`/coins/${coin.id}`}>{coin.title}</Link>
              </td>
              <td>{money(coin.price)}</td>
              <td>
                <input
                  type="number"
                  min="1"
                  max={coin.quantity_available}
                  value={quantity}
                  onChange={(e) => setQuantity(coin.id, Number(e.target.value))}
                  className="qty"
                />
              </td>
              <td>{money(Number(coin.price) * quantity)}</td>
              <td>
                <button className="link" onClick={() => remove(coin.id)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th colSpan={3}>Total</th>
            <th>{money(total)}</th>
            <th />
          </tr>
        </tfoot>
      </table>

      {error && <p className="error">{error}</p>}

      {user ? (
        <button onClick={checkout} disabled={busy}>
          {busy ? 'Placing order...' : 'Place order'}
        </button>
      ) : (
        <p className="muted">
          <button onClick={() => navigate('/login', { state: { from: '/cart' } })}>
            Sign in to check out
          </button>
        </p>
      )}
    </section>
  )
}
