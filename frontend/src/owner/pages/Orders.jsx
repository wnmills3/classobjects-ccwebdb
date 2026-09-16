import { useEffect, useState } from 'react'

import { api } from '../api'
import { date, money } from '../../shared/format'
import ModalDialog from '../ModalDialog'
import OrderEditor from './orders/OrderEditor'
import OrderHistory from './orders/OrderHistory'

/**
 * Every order, for working through them.
 *
 * The shop shows a customer their own orders. The console is a superset of
 * the shop, so the owner gets all of them here, with who placed each and
 * what is in it, and the whole status vocabulary rather than the shop's four.
 */

//: `sales_order_status` codes, in the order an order moves through them.
const STATUSES = [
  'pending',
  'paid',
  'packed',
  'shipped',
  'delivered',
  'cancelled',
  'refunded',
]

export default function Orders() {
  const [orders, setOrders] = useState(null)
  const [error, setError] = useState('')
  const [show, setShow] = useState('')
  // Bumped to fetch again; the effect is the only place the list is set.
  const [reloads, setReloads] = useState(0)
  // 'new' or an order being placed or revised; null when the editor is closed.
  const [editing, setEditing] = useState(null)
  // The order whose history dialog is open; null when it is closed.
  const [historyOf, setHistoryOf] = useState(null)

  useEffect(() => {
    let cancelled = false
    api
      .listOrders()
      .then((rows) => {
        if (!cancelled) setOrders(rows)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [reloads])

  async function changeStatus(order, status) {
    // Cancelling an unshipped order puts its stock back on sale, and the
    // server then refuses to move the order on again -- so it is asked about.
    if (
      status === 'cancelled' &&
      !window.confirm(
        `Cancel order #${order.id} for ${order.customer_name}? Unshipped stock goes ` +
          'back on sale, and a cancelled order cannot be reopened.',
      )
    ) {
      return
    }
    try {
      await api.setOrderStatus(order.id, status)
      setError('')
    } catch (err) {
      setError(err.message)
    }
    setReloads((n) => n + 1)
  }

  if (!orders && error) return <p className="error">{error}</p>
  if (!orders) return <p className="muted">Loading...</p>

  const shown = show ? orders.filter((o) => o.status === show) : orders

  return (
    <section>
      <h1>Orders</h1>

      <div className="row">
        <button onClick={() => setEditing('new')}>New order</button>
      </div>

      <div className="row">
        <label>
          Show{/* */}
          <select value={show} onChange={(e) => setShow(e.target.value)}>
            <option value="">every status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">
          {shown.length} of {orders.length}
        </span>
      </div>

      {error && <p className="error">{error}</p>}
      {shown.length === 0 && <p className="muted">No orders to show.</p>}

      {shown.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Placed</th>
              <th>Customer</th>
              <th>Items</th>
              <th>Total</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((order) => (
              <tr key={order.id}>
                <td className="mono">#{order.id}</td>
                <td>{date(order.placed_at)}</td>
                <td>
                  {order.customer_name}
                  {order.customer_email && (
                    <div className="muted">{order.customer_email}</div>
                  )}
                  {order.placed_by_email &&
                    order.placed_by_email !== order.customer_email && (
                      <div className="muted">entered by {order.placed_by_email}</div>
                    )}
                </td>
                <td>
                  {order.items.map((line) => (
                    <div key={line.id}>
                      {line.title} x {line.quantity}{' '}
                      <span className="muted">@ {money(line.unit_price)}</span>
                    </div>
                  ))}
                </td>
                <td>
                  {money(order.total_amount)}
                  {order.payment_adjustment_due && (
                    <span className="badge">payment adjustment due</span>
                  )}
                </td>
                <td>
                  <select
                    aria-label={`Status of order ${order.id}`}
                    value={order.status}
                    disabled={order.status === 'cancelled'}
                    title={
                      order.status === 'cancelled'
                        ? 'Cancelled: its stock was returned, so it cannot be reopened'
                        : undefined
                    }
                    onChange={(e) => changeStatus(order, e.target.value)}
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {['pending', 'paid'].includes(order.status) && (
                    <button className="link" onClick={() => setEditing(order)}>
                      Edit
                    </button>
                  )}{' '}
                  <button className="link" onClick={() => setHistoryOf(order)}>
                    History
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <ModalDialog label="Order" onClose={() => setEditing(null)}>
          <OrderEditor
            order={editing === 'new' ? null : editing}
            onClose={() => setEditing(null)}
            onSaved={() => {
              setEditing(null)
              setReloads((n) => n + 1)
            }}
          />
        </ModalDialog>
      )}
      {historyOf && (
        <ModalDialog label="Order history" onClose={() => setHistoryOf(null)}>
          <OrderHistory order={historyOf} onClose={() => setHistoryOf(null)} />
        </ModalDialog>
      )}
    </section>
  )
}
