import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import ItemFinder from './receiving/ItemFinder'
import ModalDialog from '../ModalDialog'
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
 * Receiving: find what arrived, then record it, one item at a time.
 *
 * One search form (`ItemFinder`), not a choice between "By order" and "By
 * item" (2026-09-23): its order number field takes part of the number, which
 * does what picking an order from a list did, and the same form finds an
 * item in hand whose order is not known.
 *
 * **`?order=<id>` still works** -- the inventory screens' Order column and
 * New purchase's "Receive these" link here that way. The order's number is
 * read and handed to the search, which runs at once with it. The search is
 * keyed on that number, so following a link to another order starts a fresh
 * search rather than keeping the last one's fields.
 *
 * `receiving` holds the line whose receipt dialog is open, together with the
 * order link it was opened under. The dialog is rendered only while that
 * still matches the address, so Back/Forward to another order's link closes
 * it rather than leaving it to submit against a search no longer on screen.
 *
 * What the last receipt was recorded against (`lastReceipt`) seeds the next
 * dialog, so a parcel of twenty into one location is not twenty identical
 * dropdown picks. After each receipt `epoch` is bumped, which repeats the
 * search: the item just received leaves the "not yet arrived" list.
 */
export default function Receiving() {
  const [params] = useSearchParams()
  const orderId = orderIdFromParams(params)
  const [linked, setLinked] = useState(null)
  // Keyed by the order it is about, like `linked`: an error for one link
  // must not stay on screen, or hold the page, once the address names
  // another order (code review, 2026-09-23).
  const [linkFailure, setLinkFailure] = useState(null)
  const [receiving, setReceiving] = useState(null)
  const [lastReceipt, setLastReceipt] = useState({})
  const [epoch, setEpoch] = useState(0)

  useEffect(() => {
    if (orderId == null) return undefined
    let cancelled = false
    api
      .getPurchaseOrder(orderId)
      .then((body) => {
        if (!cancelled) setLinked({ id: orderId, order: body })
      })
      .catch((err) => {
        if (!cancelled) setLinkFailure({ id: orderId, message: err.message })
      })
    return () => {
      cancelled = true
    }
  }, [orderId])

  // Derived, not synchronised: the stored answer counts only for the order
  // the address names now.
  const order = linked?.id === orderId ? linked.order : null
  const linkError = linkFailure?.id === orderId ? linkFailure.message : ''
  const waitingForOrder = orderId != null && order == null && !linkError

  const scope = `order:${orderId}`
  const openLine = receiving?.scope === scope ? receiving.line : null

  function handleReceiptDone(used) {
    if (used) setLastReceipt(used)
    closeReceipt()
  }

  // Every close searches again, not only a clean receipt: a receipt whose
  // photograph failed to upload keeps the dialog open for the error, and
  // is recorded all the same -- closing it must not leave the item listed
  // as not yet arrived (code review, 2026-09-23).
  function closeReceipt() {
    setReceiving(null)
    setEpoch((n) => n + 1)
  }

  return (
    <section>
      <h1>Receiving</h1>

      {linkError && <p className="error">{linkError}</p>}
      {waitingForOrder && <p className="muted">Loading...</p>}
      {/* The order a link named: who it was bought from, when, and the
          seller's page, as the order view showed before the search
          replaced it. */}
      {order && (
        <h2>
          {order.order_number} &middot; {order.vendor} &middot; {date(order.ordered_on)}
          {order.source_url && (
            <>
              {' '}
              &middot;{' '}
              <a href={order.source_url} target="_blank" rel="noopener noreferrer">
                Vendor page
              </a>
            </>
          )}
        </h2>
      )}
      {!waitingForOrder && (
        <div className="admin-form">
          <ItemFinder
            key={orderId ?? ''}
            orderId={order ? orderId : null}
            initialOrderNumber={order?.order_number ?? ''}
            epoch={epoch}
            onPick={(line) => setReceiving({ scope, line })}
          />
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
