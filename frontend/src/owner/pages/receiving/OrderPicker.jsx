/**
 * The list of purchase orders to receive against.
 *
 * Shows every order the backend returns, including one that has fully
 * arrived: hiding it here would remove the only way to look back at what
 * an order contained once everything on it was received, and `OrderLines`
 * already renders a fully-arrived order's lines just fine (all dimmed,
 * none selectable). `outstanding` (from `GET /api/purchase-orders`, which
 * counts `ordered` and `missing`) is shown as a hint, not a filter -- so an
 * order whose only receivable line is `missing` is never hidden either,
 * which a filter on `outstanding > 0` alone would still have gotten right,
 * but a fully-received order would not have.
 */
export default function OrderPicker({ orders, selectedId, onPick }) {
  if (orders.length === 0) {
    return <p className="muted">No purchase orders yet.</p>
  }

  return (
    <ul className="order-picker">
      {orders.map((order) => (
        <li
          key={order.id}
          className={
            order.id === selectedId ? 'order-row order-row-active' : 'order-row'
          }
          onClick={() => onPick(order.id)}
        >
          {order.order_number} &middot; {order.vendor} &middot; {order.outstanding} of{' '}
          {order.total}
        </li>
      ))}
    </ul>
  )
}
