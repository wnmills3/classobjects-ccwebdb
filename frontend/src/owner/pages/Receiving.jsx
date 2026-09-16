import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import ItemFinder from './receiving/ItemFinder'
import ModalDialog from '../ModalDialog'
import OrderLines from './receiving/OrderLines'
import OrderPicker from './receiving/OrderPicker'
import ReceiptPanel from './receiving/ReceiptPanel'
import { date } from '../../shared/format'

/** A positive integer `order` query parameter, or null when absent or bad. */
function orderIdFromParams(params) {
  const raw = params.get('order')
  if (!raw) return null
  const n = Number(raw)
  return Number.isInteger(n) && n > 0 ? n : null
}

/**
 * Receiving: pick a purchase order, then record what arrived, one line at a
 * time.
 *
 * The picked order's detail is stored keyed by the order id it answers, the
 * same shape `useInventorySearch` uses for its result: "still loading" is
 * then *derived* as "the stored answer isn't for the order currently picked",
 * rather than a second flag that could disagree with it. A `cancelled` guard
 * on top of that stops a slow response for an order the owner already
 * clicked past from landing after a faster, later one -- clicking through
 * three orders quickly is exactly the case that would otherwise show stale
 * lines under the wrong order.
 *
 * `receiving` holds the line whose receipt dialog is open -- the whole line,
 * so the dialog can name it -- and null when none is. The `ReceiptPanel`
 * inside it is the same component both ways in, handed exactly one id: the
 * order path picks a line off the table, and `ItemFinder`'s attribute search
 * is the fallback for when the object is in hand and which order it came
 * from is not known. Both only ever hand it something still outstanding, so
 * it never has to know which path found it.
 *
 * **The `order` query parameter scopes the page.** With `?order=<id>` --
 * which is how the inventory screens' Order column and New purchase's
 * "Receive these" both link here -- the picker is not shown at all and only
 * that order's lines are, so following a link about one purchase does not
 * open onto a list of every other one. Without it, the picker is the way in.
 * Either way the parameter names what is open, so Back/Forward move between
 * orders and the address bar can be copied.
 */
export default function Receiving() {
  const [params, setParams] = useSearchParams()
  const [orders, setOrders] = useState(null)
  const [ordersError, setOrdersError] = useState('')
  const orderId = orderIdFromParams(params)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState('')
  const [receiving, setReceiving] = useState(null)
  const [mode, setMode] = useState('order')

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
  const reloadOrder = useCallback((id) => {
    let cancelled = false

    api
      .getPurchaseOrder(id)
      .then((body) => {
        if (cancelled) return
        setDetail({ id, body })
        setDetailError('')
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
    return reloadOrder(orderId)
  }, [orderId, reloadOrder])

  const order = detail?.id === orderId ? detail.body : null
  const loadingOrder = orderId != null && order == null && !detailError

  // The dialog closes on success and the order is pulled again, so the line
  // just recorded reappears in its new status rather than still reading
  // `ordered` behind a dialog that has already gone.
  function handleReceiptDone() {
    setReceiving(null)
    if (mode === 'order' && orderId != null) reloadOrder(orderId)
  }

  // An open dialog has no business surviving a switch between the two ways
  // in. `detail` stays: the fetch effect only refires when `orderId` changes,
  // so clearing it here with `orderId` unchanged would strand the order view
  // on "Loading..." forever instead of ever re-fetching.
  function switchMode(next) {
    setMode(next)
    setReceiving(null)
  }

  // Puts the pick in the URL -- the only place `orderId` is held -- so
  // opening Receiving from a link elsewhere reopens it directly, and
  // Back/Forward move between picks.
  function pickOrder(id) {
    setParams({ order: String(id) })
  }

  function clearOrder() {
    setParams({})
  }

  return (
    <section>
      <h1>Receiving</h1>

      <div className="filter-grid">
        <label className="checkbox">
          <input
            type="radio"
            name="receiving-mode"
            value="order"
            checked={mode === 'order'}
            onChange={() => switchMode('order')}
          />
          By order
        </label>
        <label className="checkbox">
          <input
            type="radio"
            name="receiving-mode"
            value="search"
            checked={mode === 'search'}
            onChange={() => switchMode('search')}
          />
          By item
        </label>
      </div>

      {mode === 'order' && (
        <>
          {orderId == null && (
            <>
              {ordersError && <p className="error">{ordersError}</p>}
              {!ordersError && !orders && <p className="muted">Loading...</p>}
              {orders && (
                <OrderPicker orders={orders} selectedId={null} onPick={pickOrder} />
              )}
            </>
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
                    {order.source_url && (
                      <>
                        {' '}
                        &middot;{' '}
                        <a
                          href={order.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Vendor page
                        </a>
                      </>
                    )}
                  </h2>
                  <OrderLines lines={order.lines} onPick={setReceiving} />
                </>
              )}
              <button type="button" className="link" onClick={clearOrder}>
                Choose another order
              </button>
            </div>
          )}
        </>
      )}

      {mode === 'search' && (
        <div className="admin-form">
          <ItemFinder onPick={setReceiving} />
        </div>
      )}

      {receiving && (
        <ModalDialog
          label={`Receive ${receiving.item_code}`}
          onClose={() => setReceiving(null)}
        >
          <h2>
            <span className="mono">{receiving.item_code}</span>{' '}
            {receiving.source_title || receiving.description}
          </h2>
          <ReceiptPanel itemIds={[receiving.id]} onDone={handleReceiptDone} />
          <button type="button" className="link" onClick={() => setReceiving(null)}>
            Close
          </button>
        </ModalDialog>
      )}
    </section>
  )
}
