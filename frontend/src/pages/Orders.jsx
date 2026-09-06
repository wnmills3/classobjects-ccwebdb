import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'
import { useAuth } from '../auth'
import { date, money } from '../format'

const STATUSES = ['pending', 'paid', 'shipped', 'cancelled']

export default function Orders() {
  const { isAdmin } = useAuth()
  const [orders, setOrders] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      setOrders(await api.listOrders())
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function changeStatus(id, status) {
    try {
      await api.setOrderStatus(id, status)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (busy) return <p className="muted">Loading...</p>

  return (
    <section>
      <h1>{isAdmin ? 'All orders' : 'Your orders'}</h1>
      {error && <p className="error">{error}</p>}
      {orders.length === 0 && <p className="muted">No orders yet.</p>}

      {orders.map((order) => (
        <article key={order.id} className="order">
          <header>
            <strong>Order #{order.id}</strong>
            <span className={`status status-${order.status}`}>{order.status}</span>
            <span className="muted small">{date(order.created_at)}</span>
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
                  <td>#{item.listing_id}</td>
                  <td>{item.quantity}</td>
                  <td>{money(item.unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {isAdmin && (
            <div className="row">
              <label>
                Status
                <select
                  value={order.status}
                  onChange={(e) => changeStatus(order.id, e.target.value)}
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <span className="muted small">
                Cancelling returns the items to available stock.
              </span>
            </div>
          )}
        </article>
      ))}
    </section>
  )
}
