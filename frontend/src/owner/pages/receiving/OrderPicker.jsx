/**
 * The list of purchase orders still worth receiving against.
 *
 * The backend returns every order regardless of how much of it has arrived
 * (that view is cheap to compute once for the whole list); an order with
 * nothing outstanding is filtered out here rather than there, since "is this
 * one worth showing" is a display decision, not a fact about the order.
 */
export default function OrderPicker({ orders, selectedId, onPick }) {
  const outstanding = orders.filter((order) => order.outstanding > 0)

  if (outstanding.length === 0) {
    return (
      <p className="muted">Nothing outstanding -- every order has fully arrived.</p>
    )
  }

  return (
    <ul className="order-picker">
      {outstanding.map((order) => (
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
