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
 * what is in it, and a picker over the whole status vocabulary, which the
 * shop only displays.
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

//: The statuses from which cancelling returns no stock, mirroring
//: `routers.orders.NO_STOCK_RETURN_STATUSES` (less `cancelled`, which is
//: disabled on its own). The goods have left, or the money went back, so
//: nothing is put back and nothing can be stranded. Kept in the same order
//: as STATUSES above for reading, not because anything here depends on it.
const NO_STOCK_RETURN_STATUSES = new Set(['packed', 'shipped', 'delivered', 'refunded'])

//: Orders asked for at a time. The server pages the list (`limit`, `offset`)
//: and says how many there are in all.
const PAGE_SIZE = 100

/**
 * Why the server would refuse moving `order` to `status`, or null when it
 * would not. Every option stays live; choosing a refused one sends nothing
 * and shows this instead, the reason the server's 409 would give.
 *
 * A cancelled order's stock is back on sale, so it cannot be moved on.
 * Cancelling or refunding an unshipped order returns its stock, which has
 * nowhere to go when a listing has ended: a sale recorded from another
 * platform ended its listing, and a lot bought in the shop ends its listing
 * at checkout (each line says so, `listing_ended`). Once shipped, either
 * returns no stock -- cancelling is how a refund is recorded, and an
 * `auction_house` sale is created `delivered` -- so it is not refused.
 * "Unshipped" is read here from the current status; the server reads the
 * order's history as well, so an order set back from `shipped` is refused
 * here though the server would take it.
 */
function refusal(order, status) {
  if (order.status === 'cancelled' && status !== 'cancelled') {
    return (
      `Order #${order.id} is cancelled and its stock has been returned. ` +
      'Place a new order instead.'
    )
  }
  const returnsStock = status === 'cancelled' || status === 'refunded'
  if (!returnsStock || NO_STOCK_RETURN_STATUSES.has(order.status)) return null
  if (order.sales_venue_code !== 'store') {
    return (
      `Sold on ${order.sales_venue_name}, whose listing ended with the sale ` +
      `-- there is no stock to return, so order #${order.id} cannot be ${status} ` +
      'before it ships.'
    )
  }
  if (order.items.some((line) => line.listing_ended)) {
    return (
      'Its listing has ended (a lot ends when it is bought) -- there is no ' +
      `stock to return, so order #${order.id} cannot be ${status} before it ships.`
    )
  }
  return null
}

/**
 * The Sales page: the orders a page at a time, newest first, filtered by
 * status on the server, each with a status picker, its history, and an
 * editor while it is pending or paid.
 *
 * A status is saved as it is chosen, and the order the server answers with
 * takes its row's place. A change that would put stock back on sale -- a
 * cancellation, or a refund before shipping -- is asked about first, and is
 * refused with its reason where there is no listing to return it to. A
 * refused change, and a placed or revised order, read the page again.
 */
export default function Orders() {
  // `{rows, total}` for the page on screen; null until the first answer.
  const [page, setPage] = useState(null)
  // The latest load failed. Cleared by the next one that succeeds.
  const [loadError, setLoadError] = useState('')
  // A failed status change, which stays said while the list reloads.
  const [actionError, setActionError] = useState('')
  const error = actionError || loadError
  const [show, setShow] = useState('')
  const [offset, setOffset] = useState(0)
  // Bumped to read the page again after a write.
  const [reloads, setReloads] = useState(0)
  // 'new' or an order being placed or revised; null when the editor is closed.
  const [editing, setEditing] = useState(null)
  // The order whose history dialog is open; null when it is closed.
  const [historyOf, setHistoryOf] = useState(null)

  // Held here rather than in a `useRequest`, which would own the list: a
  // status change puts its one order back in place without asking again.
  useEffect(() => {
    let cancelled = false
    api
      .listOrders({ status: show, limit: PAGE_SIZE, offset })
      .then((answer) => {
        if (cancelled) return
        setPage(answer)
        setLoadError('')
      })
      .catch((err) => !cancelled && setLoadError(err.message))
    return () => {
      cancelled = true
    }
  }, [show, offset, reloads])

  const reload = () => setReloads((n) => n + 1)

  async function changeStatus(order, status) {
    const refused = refusal(order, status)
    if (refused) {
      setActionError(refused)
      return
    }
    // Cancelling an unshipped order puts its stock back on sale, and the
    // server then refuses to move the order on again -- so it is asked about.
    // Refunding one before it ships does the same.
    if (
      status === 'cancelled' &&
      !window.confirm(
        `Cancel order #${order.id} for ${order.customer_name}? Unshipped stock goes ` +
          'back on sale, and a cancelled order cannot be reopened.',
      )
    ) {
      return
    }
    if (
      status === 'refunded' &&
      !NO_STOCK_RETURN_STATUSES.has(order.status) &&
      !window.confirm(
        `Refund order #${order.id} for ${order.customer_name}? It has not shipped, ` +
          'so its stock goes back on sale and the order cannot then be shipped.',
      )
    ) {
      return
    }
    try {
      // The version the list was read at, so a status chosen from a page
      // someone else has since moved on is refused rather than applied.
      const saved = await api.setOrderStatus(order.id, status, order.version)
      setActionError('')
      // A status change touches that order and no other, so its row takes
      // the answer -- new status and version -- and the page is not read
      // again. It stays on screen under a status filter it no longer
      // matches, showing what was just done, until the next load.
      setPage((current) => ({
        ...current,
        rows: current.rows.map((o) => (o.id === saved.id ? saved : o)),
      }))
    } catch (err) {
      setActionError(err.message)
      // Refused, often because someone else moved the order on: read the
      // page again to show where it stands now.
      reload()
    }
  }

  if (!page && error) return <p className="error">{error}</p>
  if (!page) return <p className="muted">Loading...</p>

  const shown = page.rows
  // The header gives the count across every page; without it, this page's.
  const total = page.total ?? offset + shown.length
  const counted =
    offset === 0 && shown.length === total
      ? `${total} of ${total}`
      : `${shown.length === 0 ? 0 : offset + 1}-${offset + shown.length} of ${total}`

  return (
    <section>
      <h1>Sales</h1>

      <div className="row">
        <button onClick={() => setEditing('new')}>New order</button>
      </div>

      <div className="row">
        <label>
          Show{/* */}
          <select
            value={show}
            onChange={(e) => {
              setShow(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">every status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{counted}</span>
        {/* Shown only where there is a page to go to, rather than greyed
            out where there is none. */}
        {offset > 0 && (
          <button
            className="link"
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Newer
          </button>
        )}
        {offset + shown.length < total && (
          <button className="link" onClick={() => setOffset(offset + PAGE_SIZE)}>
            Older
          </button>
        )}
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
                    onChange={(e) => changeStatus(order, e.target.value)}
                  >
                    {/* Every status stays live: one the server would refuse
                        is answered with its reason when chosen (`refusal`). */}
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
              reload()
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
