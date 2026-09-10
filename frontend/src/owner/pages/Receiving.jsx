import { useEffect, useState } from 'react'

import { api } from '../api'
import OrderPicker from './receiving/OrderPicker'
import OutstandingList from './receiving/OutstandingList'
import { date } from '../../shared/format'

/**
 * Receiving: pick a purchase order, see what on it has not arrived yet.
 *
 * `selected` (the checked line ids) is held here rather than inside
 * `OutstandingList`, so a later action bar -- recording arrival, choosing a
 * storage location -- can read it without the list needing to know that
 * anything downstream exists.
 *
 * The picked order's detail is stored keyed by the order id it answers, the
 * same shape `useInventorySearch` uses for its result: "still loading" is
 * then *derived* as "the stored answer isn't for the order currently picked",
 * rather than a second flag that could disagree with it. A `cancelled` guard
 * on top of that stops a slow response for an order the owner already
 * clicked past from landing after a faster, later one -- clicking through
 * three orders quickly is exactly the case that would otherwise show stale
 * lines under the wrong order.
 */
export default function Receiving() {
  const [orders, setOrders] = useState(null)
  const [ordersError, setOrdersError] = useState('')
  const [orderId, setOrderId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState('')
  const [selected, setSelected] = useState([])

  useEffect(() => {
    let cancelled = false

    api
      .listPurchaseOrders()
      .then((body) => {
        if (cancelled) return
        setOrders(body)
        setOrdersError('')
      })
      .catch((err) => {
        if (!cancelled) setOrdersError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (orderId == null) return undefined
    let cancelled = false

    api
      .getPurchaseOrder(orderId)
      .then((body) => {
        if (cancelled) return
        setDetail({ id: orderId, body })
        setDetailError('')
        setSelected([])
      })
      .catch((err) => {
        if (!cancelled) setDetailError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [orderId])

  const order = detail?.id === orderId ? detail.body : null
  const loadingOrder = orderId != null && order == null && !detailError

  return (
    <section>
      <h1>Receiving</h1>

      {ordersError && <p className="error">{ordersError}</p>}
      {!ordersError && !orders && <p className="muted">Loading...</p>}
      {orders && (
        <OrderPicker orders={orders} selectedId={orderId} onPick={setOrderId} />
      )}

      {orderId != null && (
        <div className="admin-form">
          {detailError && <p className="error">{detailError}</p>}
          {loadingOrder && <p className="muted">Loading...</p>}
          {order && (
            <>
              <h2>
                {order.order_number} &middot; {order.vendor} &middot;{' '}
                {date(order.ordered_on)}
              </h2>
              <OutstandingList
                lines={order.lines}
                selected={selected}
                onChange={setSelected}
              />
            </>
          )}
        </div>
      )}
    </section>
  )
}
