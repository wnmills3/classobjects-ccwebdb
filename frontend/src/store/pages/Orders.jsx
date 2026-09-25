import { useEffect, useState } from 'react'

import { api } from '../../shared/api'
import { date, money } from '../../shared/format'

/**
 * The shopper's own orders.
 *
 * Only their own, whoever is signed in. This page used to turn into "All
 * orders" with a status control for an administrator; that is the owner
 * console's Sales page now, and the shop keeps no admin view.
 */
export default function Orders() {
  const [orders, setOrders] = useState([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listMyOrders()
      .then((rows) => {
        if (cancelled) return
        setOrders(rows)
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!loaded) return <p className="muted">Loading...</p>

  return (
    <section>
      <h1>Your orders</h1>
      {error && <p className="error">{error}</p>}
      {!error && orders.length === 0 && <p className="muted">No orders yet.</p>}

      {orders.map((order) => (
        <article key={order.id} className="order">
          <header>
            <strong>Order #{order.id}</strong>
            <span className={`status status-${order.status}`}>{order.status}</span>
            <span className="muted small">{date(order.placed_at)}</span>
            <span className="grow" />
            <strong>{money(order.total_amount)}</strong>
          </header>

          <table className="table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Qty</th>
                <th>Unit price</th>
              </tr>
            </thead>
            <tbody>
              {order.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.title}</td>
                  <td>{item.quantity}</td>
                  <td>{money(item.unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      ))}
    </section>
  )
}
