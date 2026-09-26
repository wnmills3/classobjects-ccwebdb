import { api } from '../../shared/api'
import { date, money } from '../../shared/format'
import { useRequest } from '../../shared/useRequest'

/**
 * The shopper's own orders.
 *
 * Only their own, whoever is signed in, an administrator included. Every
 * order, and changing an order's status, is the console's Sales page; the
 * shop has no admin view.
 */
export default function Orders() {
  const { data, error, busy } = useRequest('mine', () => api.listMyOrders())
  const orders = data ?? []

  if (busy) return <p className="muted">Loading...</p>

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
