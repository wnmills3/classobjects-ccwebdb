import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../../shared/api'
import { useAuth } from '../../shared/auth-context'
import { useCart } from '../cart-context'
import { fromCents, isMoney, toCents } from '../../shared/cents'
import { money } from '../../shared/format'

/**
 * A line's quantity box.
 *
 * It holds what is typed, and the cart takes it only when it is a whole
 * number of at least one. Passed straight through, clearing the box to type
 * a new number would be a quantity of zero -- a removal -- and "1.5" would be
 * a quantity. Leaving the box shows the cart's quantity again; Remove is the
 * way to drop a line. A number past the stock on hand is taken as the stock,
 * which the box then shows, and the line says how many there are.
 */
function QuantityInput({ coin, quantity, onChange }) {
  const [draft, setDraft] = useState(null)
  // Whether the last number typed was more than the stock on hand.
  const [capped, setCapped] = useState(false)

  function change(event) {
    const text = event.target.value
    const whole = /^\d+$/.test(text) && Number(text) >= 1
    // The cart holds a line to the stock. A number past it is not kept in
    // the box, which then shows what the cart took, with the reason beside.
    const over = whole && Number(text) > coin.quantity_available
    setCapped(over)
    setDraft(over ? null : text)
    if (whole) onChange(Number(text))
  }

  return (
    <>
      <input
        type="number"
        min="1"
        step="1"
        max={coin.quantity_available}
        value={draft ?? quantity}
        onChange={change}
        onBlur={() => setDraft(null)}
        className="qty"
        aria-label={`Quantity of ${coin.title}`}
      />
      {capped && (
        <output className="muted small">
          Only {coin.quantity_available} available.
        </output>
      )}
    </>
  )
}

/**
 * The cart page: a line per listing with its quantity and line total, the
 * total, and the button that places the order.
 *
 * Placing an order needs a sign-in, so a visitor is sent to sign in and
 * brought back here. An order the server accepts empties the cart and is
 * thanked for by number; a refusal -- the stock went meanwhile -- is shown
 * and the cart is kept.
 */
export default function Cart() {
  const { lines, setQuantity, remove, clear, total } = useCart()
  // The total is in the lines' own currency. Lines in two currencies have no
  // meaningful sum, so it shows as absent rather than as dollars.
  const currencies = [...new Set(lines.map(({ coin }) => coin.currency || 'USD'))]
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
          Your cart is empty. <Link to="/">Browse the catalog</Link>.
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
              <td>{money(coin.price, coin.currency)}</td>
              <td>
                <QuantityInput
                  coin={coin}
                  quantity={quantity}
                  onChange={(next) => setQuantity(coin.id, next)}
                />
              </td>
              <td>
                {isMoney(coin.price)
                  ? money(fromCents(toCents(coin.price) * quantity), coin.currency)
                  : money(null)}
              </td>
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
            <th>{currencies.length > 1 ? money(null) : money(total, currencies[0])}</th>
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
