import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'
import OrderPicker from './receiving/OrderPicker'
import OutstandingList from './receiving/OutstandingList'
import ReceiptPanel from './receiving/ReceiptPanel'
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

  // Extracted so a receipt can pull the same order again once it lands,
  // without duplicating the fetch-and-set-state dance a second time.
  // `resetSelection` only fires once the fetch actually lands -- setting
  // state synchronously in the effect body itself is what
  // react-hooks/set-state-in-effect warns against.
  const reloadOrder = useCallback((id, { resetSelection = false } = {}) => {
    let cancelled = false

    api
      .getPurchaseOrder(id)
      .then((body) => {
        if (cancelled) return
        setDetail({ id, body })
        setDetailError('')
        if (resetSelection) setSelected([])
      })
      .catch((err) => {
        if (!cancelled) setDetailError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (orderId == null) return undefined
    return reloadOrder(orderId, { resetSelection: true })
  }, [orderId, reloadOrder])

  const order = detail?.id === orderId ? detail.body : null
  const loadingOrder = orderId != null && order == null && !detailError

  // A received item drops off the outstanding list, so the ids just
  // submitted would otherwise linger in `selected` and be resubmitted --
  // already `received`, which the backend answers with a 409.
  function handleReceiptDone() {
    setSelected([])
    if (orderId != null) reloadOrder(orderId)
  }

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
              <ReceiptPanel itemIds={selected} onDone={handleReceiptDone} />
            </>
          )}
        </div>
      )}
    </section>
  )
}
