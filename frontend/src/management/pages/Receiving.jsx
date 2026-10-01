import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import ItemFinder from './receiving/ItemFinder'
import ModalDialog from '../ModalDialog'
import { purchaseNumber } from '../purchase-number'
import ReceiptPanel from './receiving/ReceiptPanel'
import { date } from '../../shared/format'
import { useRequest } from '../../shared/useRequest'

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
 * Purchases' "Receive these" link here that way. The order's number is
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
 *
 * **Receive all** (owner, 2026-09-30): on a linked order with two or more
 * lines still `ordered`, one button opens the same dialog over all of them --
 * one arrival date, one location, one all-or-nothing request. It is the
 * parcel that arrived whole; a split shipment is still received line by line.
 * The order is read again after every receipt (`epoch` is in its key), so
 * the count never offers a line that has just arrived; the server's 409 on
 * an already-received item is the backstop.
 */
export default function Receiving() {
  const [params] = useSearchParams()
  const orderId = orderIdFromParams(params)
  const [receiving, setReceiving] = useState(null)
  const [lastReceipt, setLastReceipt] = useState({})
  const [epoch, setEpoch] = useState(0)

  // Only the answer for the order the address names now counts. `useRequest`
  // reports an error only for the current key, so another link's failure
  // leaves the page once the address names a different order or none. Its
  // `data` is kept while the next order loads, so `order` also waits on
  // `busy` rather than showing the previous order's header.
  //
  // Read again after every receipt, so "still ordered" stays true. Only the
  // first read of an order is waiting: a re-read keeps this order's answer
  // on screen, and must not unmount the search under the operator.
  const link = useRequest(orderId == null ? null : `${orderId}:${epoch}`, () =>
    api.getPurchaseOrder(orderId),
  )
  const waitingForOrder = link.busy && link.data?.id !== orderId
  const linkError = link.error
  const order = orderId != null && !waitingForOrder && !linkError ? link.data : null

  const scope = `order:${orderId}`
  const openLine = receiving?.scope === scope ? receiving.line : null
  // Offered only from a settled read, never from the previous one.
  const stillOrdered =
    order && !link.busy ? order.lines.filter((line) => line.status === 'ordered') : []
  const openAll = receiving?.scope === scope && receiving.all ? receiving.all : null

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
          {purchaseNumber(order.id)} &middot; {order.order_number} &middot;{' '}
          {order.vendor} &middot; {date(order.ordered_on)}
          {order.source_url && (
            <>
              {' '}
              &middot;{' '}
              <a href={order.source_url} target="_blank" rel="noopener noreferrer">
                Vendor page
              </a>
            </>
          )}
          {order.seller && (
            <>
              {' '}
              &middot; Seller{' '}
              {order.seller_url ? (
                <a href={order.seller_url} target="_blank" rel="noopener noreferrer">
                  {order.seller}
                </a>
              ) : (
                order.seller
              )}
            </>
          )}
        </h2>
      )}
      {stillOrdered.length > 1 && (
        <p>
          <button
            type="button"
            onClick={() => setReceiving({ scope, all: stillOrdered.map((l) => l.id) })}
          >
            Receive all {stillOrdered.length} still ordered
          </button>
        </p>
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

      {openAll && (
        <ModalDialog
          label={`Receive ${openAll.length} items on ${purchaseNumber(orderId)}`}
          onClose={closeReceipt}
        >
          <h2>
            All {openAll.length} items still ordered on {purchaseNumber(orderId)}
          </h2>
          <ReceiptPanel
            itemIds={openAll}
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
