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
 * so the dialog can name it -- together with the `scope` it was opened from.
 * The dialog is rendered only while that scope still matches what is on
 * screen, which is the same "derive, don't synchronise" rule `detail` uses by
 * being keyed on the order id it answers. Held as a bare line instead, the
 * dialog outlived its order: Back/Forward and `?order=` links do not go
 * through any handler here, so an open dialog for order 1's line stayed up
 * over order 2's table and would submit against it -- then reload the order
 * on screen, so the line actually received was never refreshed anywhere.
 * Scoping it also means switching between the two ways in closes it, with no
 * second piece of state to remember to clear.
 *
 * The `ReceiptPanel` inside is the same component both ways in, handed
 * exactly one id: the order path picks a line off the table, and
 * `ItemFinder`'s attribute search is the fallback for when the object is in
 * hand and which order it came from is not known. Both only ever hand it
 * something still outstanding, so it never has to know which path found it.
 *
 * What the last receipt was recorded against (`lastReceipt`) seeds the next
 * dialog, so a parcel of twenty into one location is not twenty identical
 * dropdown picks. The note is not carried -- see `ReceiptPanel`.
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
  const [lastReceipt, setLastReceipt] = useState({})
  //: Bumped to re-run the order fetch through its effect, which is what
  //: gives that fetch a cancel function. See `handleReceiptDone`.
  const [detailEpoch, setDetailEpoch] = useState(0)
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
  }, [orderId, reloadOrder, detailEpoch])

  const order = detail?.id === orderId ? detail.body : null
  const loadingOrder = orderId != null && order == null && !detailError

  //: What the open dialog must still belong to. The order path is scoped to
  //: the order on screen; the search path has no order, so it is its own
  //: scope and a mode switch changes it.
  const scope = mode === 'order' ? `order:${orderId}` : 'search'
  const openLine = receiving?.scope === scope ? receiving.line : null

  function openReceipt(line) {
    setReceiving({ scope, line })
  }

  function closeReceipt() {
    setReceiving(null)
  }

  // The dialog closes on success and the order is pulled again, so the line
  // just recorded reappears in its new status rather than still reading
  // `ordered` behind a dialog that has already gone.
  //
  // Through the effect, not by calling `reloadOrder` here: it returns a
  // cancel function and only an effect will call it. Left uncancelled, a
  // reload landing after a later order's fetch writes `detail` for the
  // previous order, which makes `order` null and `loadingOrder` true with no
  // effect left to re-fire -- the page then sits on "Loading..." for good.
  // One receipt per line means one such fetch per line, so an uncancelled
  // one is twenty chances at that, not one.
  function handleReceiptDone(used) {
    setReceiving(null)
    if (used) setLastReceipt(used)
    if (mode === 'order' && orderId != null) setDetailEpoch((n) => n + 1)
  }

  // `detail` is deliberately left alone: the fetch effect only refires when
  // `orderId` or the epoch changes, so clearing it here with `orderId`
  // unchanged would strand the order view on "Loading..." forever instead of
  // ever re-fetching. An open dialog needs no clearing either -- it is scoped
  // to the mode and stops rendering on its own.
  function switchMode(next) {
    setMode(next)
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
              {orders && <OrderPicker orders={orders} onPick={pickOrder} />}
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
                  <OrderLines lines={order.lines} onPick={openReceipt} />
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
          <ItemFinder onPick={openReceipt} />
        </div>
      )}

      {openLine && (
        <ModalDialog label={`Receive ${openLine.item_code}`} onClose={closeReceipt}>
          <h2>
            <span className="mono">{openLine.item_code}</span>{' '}
            {openLine.source_title || openLine.description}
          </h2>
          <ReceiptPanel
            itemIds={[openLine.id]}
            initial={lastReceipt}
            onDone={handleReceiptDone}
          />
          <button type="button" className="link" onClick={closeReceipt}>
            Close
          </button>
        </ModalDialog>
      )}
    </section>
  )
}
