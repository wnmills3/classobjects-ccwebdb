import { useEffect, useRef, useState } from 'react'

import { AccessLabel } from '../AccessLabel'
import { api } from '../api'
import ModalDialog from '../ModalDialog'
import { accel, useSaveShortcut } from '../shortcuts'
import { fromCents, isMoney, toCents } from './orders/cents'
import { UNKNOWN, subjectOf } from './listing-labels'
import { useReference } from '../../shared/reference-context'

/**
 * Recording a sale that happened on an outside platform -- eBay, Whatnot, an
 * auction house -- with the platform's actual fees.
 *
 * `POST /api/listings/{id}/sale` both creates the order and ends the listing
 * (`sales_writes.record_sale`), so this dialog only ever appears for an
 * active listing: `record_sale` itself refuses anything else with a 409 that
 * names the listing's real status.
 *
 * Money crosses this dialog exactly as it is typed and exactly as it is
 * sent: a decimal string, never rounded through a JavaScript number. The one
 * calculation here -- the gross/fees/net/margin summary -- is done in whole
 * cents via `../orders/cents.js`, for display only; what reaches
 * `api.recordSale` is the strings the owner typed, untouched.
 */

/**
 * Alt+letter for each top-level field, as every other console edit window
 * has (`docs/system-administration.md`). A fee row has no accesskey of its
 * own -- the kinds repeat, and an access key must be unique in the document
 * -- so only the fields that appear once carry one, the same choice
 * `OfferDialog.jsx`'s per-item rows make.
 *
 * No letter is D, E or F: Chrome and Edge keep those for the address bar and
 * menus on Windows. Save is not V here -- "Record sale" has no V -- so it is
 * C, the letter that survives in both "Record sale" and "Recording...".
 */
const KEYS = {
  price: 'r',
  buyer: 'b',
  orderId: 'n',
  save: 'c',
}

/** Whole cents as a decimal string, negative ones included -- a lot priced
 * under a platform's fixed fee can genuinely net less than zero. */
function money(cents) {
  return cents < 0 ? `-${fromCents(-cents)}` : fromCents(cents)
}

/** A typed amount, in cents, or zero when it is blank or not an amount. */
function centsOf(text) {
  const trimmed = String(text ?? '').trim()
  return isMoney(trimmed) ? toCents(trimmed) : 0
}

export default function RecordSaleDialog({
  listing,
  isAuctionHouse = false,
  onRecorded,
  onClose,
}) {
  const [price, setPrice] = useState('')
  const [buyer, setBuyer] = useState('')
  const [orderId, setOrderId] = useState('')
  // One typed amount per fee kind code, keyed by `code`. A kind with no key
  // here, or a blank value, was never entered and is left out of the
  // request -- "not charged" and "nobody typed this" must not collapse into
  // the same fee line.
  const [feeAmounts, setFeeAmounts] = useState({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const feeKinds = useReference('sales_fee_kind') ?? []

  // Guards save()'s continuation once the request settles: Cancel (and
  // Escape, which ModalDialog routes to onClose) can unmount this dialog
  // while a save is still in flight.
  //
  // The setup ARMS it; only the cleanup disarms it. The console runs in
  // StrictMode (`management/main.jsx`), where React runs every effect setup,
  // cleanup, setup on mount: a ref only initialized at `useRef(true)` would
  // be left false by that first cleanup for the rest of the dialog's life,
  // and a sale the server accepted would never reach `onRecorded`, leaving
  // the button reading "Recording..." forever.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // `save` is a function declaration below, hoisted for the whole component
  // scope. Disabled while a save is in flight, so holding Ctrl+S cannot fire
  // a second sale behind the first.
  useSaveShortcut(save, !saving)

  const setFee = (code) => (e) =>
    setFeeAmounts((current) => ({ ...current, [code]: e.target.value }))

  /** The fee lines the request will carry: every kind with a non-blank,
   * valid amount. A kind left blank is simply absent from `fees` -- that is
   * how "not charged this time" is spelled, matching what `record_sale`
   * itself receives. */
  function typedFees() {
    return feeKinds
      .map((kind) => ({ kind, text: String(feeAmounts[kind.code] ?? '').trim() }))
      .filter(({ text }) => text !== '')
  }

  async function save() {
    const trimmedPrice = price.trim()
    if (!isMoney(trimmedPrice)) {
      // Said here rather than left to the API, whose refusal for a blank or
      // malformed price is a schema complaint about a Decimal -- true, and
      // no help to someone who has not finished typing.
      setError('Sale price must be an amount like 115.00.')
      return
    }
    const entered = typedFees()
    const badFee = entered.find(({ text }) => !isMoney(text))
    if (badFee) {
      setError(`${badFee.kind.label} fee must be an amount like 20.35.`)
      return
    }
    setSaving(true)
    setError('')
    try {
      const sale = await api.recordSale(listing.id, {
        price: trimmedPrice,
        buyer_username: buyer.trim() === '' ? null : buyer.trim(),
        external_order_id: orderId.trim() === '' ? null : orderId.trim(),
        fees: entered.map(({ kind, text }) => ({ kind: kind.code, amount: text })),
        // `false` is the spec's default: a line's money is divided among the
        // items it carried by each one's cost basis, and **equal** is only
        // ever chosen explicitly. For a single-item sale the two are the
        // same thing, which is what this said when no lot could be sold.
        // A lot listing can now be sold here, so the choice is real -- and
        // this dialog deliberately offers no control for it. Cost-weighted
        // is the right default, an equal division is the rarer case, and
        // adding a control nobody has asked for would put a question in
        // front of every outside sale. The gap is recorded in
        // `docs/specs/selling-design.md` under *Known limits*.
        equal_shares: false,
      })
      if (!mounted.current) return
      onRecorded(sale)
    } catch (err) {
      if (!mounted.current) return
      // `err.message` is always a flattened string, whichever of the two
      // 422 shapes this endpoint answers with: `shared/api.js`'s `send`
      // already reduces a Pydantic error list to one line before the
      // `ApiError` is thrown, and leaves a plain-string refusal (409, or the
      // narrower `SaleInputInvalid` 422) untouched. Reading `err.body.detail`
      // here instead would work for one shape and hand React a raw array of
      // error objects for the other -- not a string, so not something a
      // `{error}` paragraph can render at all.
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const priceCents = isMoney(price.trim()) ? centsOf(price) : null
  const feeTotalCents = feeKinds.reduce(
    (sum, k) => sum + centsOf(feeAmounts[k.code]),
    0,
  )
  const netCents = priceCents === null ? null : priceCents - feeTotalCents
  const costText = String(listing.cost_basis ?? '')
  const hasCost = isMoney(costText)
  const margin =
    priceCents !== null && priceCents !== 0 && hasCost
      ? String(Math.round(((netCents - toCents(costText)) * 1000) / priceCents) / 10)
      : ''

  // `subjectOf`, not `item_code`. The Listings page offers "Record sale..."
  // on any active row that is not the shop's own, and a lot listing on eBay
  // is exactly such a row: this read "Record sale of null on eBay" -- on the
  // window that enters the money.
  const label = `Record sale of ${subjectOf(listing)} on ${listing.venue_name}`

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Sale price" accessKey={KEYS.price} />
          {/* Text, not number: money crosses the API as a decimal string and
              a number input hands back a float. */}
          <input
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            {...accel(KEYS.price)}
          />
        </label>
        <label>
          <AccessLabel text="Buyer username" accessKey={KEYS.buyer} />
          <input
            value={buyer}
            onChange={(e) => setBuyer(e.target.value)}
            {...accel(KEYS.buyer)}
          />
        </label>
        <label>
          <AccessLabel text="Order number" accessKey={KEYS.orderId} />
          <input
            value={orderId}
            onChange={(e) => setOrderId(e.target.value)}
            placeholder="the platform's own order or transaction number"
            {...accel(KEYS.orderId)}
          />
        </label>
      </div>
      <p className="muted">
        {isAuctionHouse
          ? `Leave the buyer blank for ${listing.venue_name}'s undisclosed buyer -- the one used whenever this auction house does not name who bought a lot.`
          : 'Leave the buyer blank if the platform does not name who bought it.'}
      </p>
      <table>
        <thead>
          <tr>
            <th>Fee</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {feeKinds.map((kind) => (
            <tr key={kind.code}>
              <td>{kind.label}</td>
              <td>
                <input
                  inputMode="decimal"
                  aria-label={kind.label}
                  value={feeAmounts[kind.code] ?? ''}
                  onChange={setFee(kind.code)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="summary">
        <div>
          <dt>Gross</dt>
          <dd>{priceCents === null ? UNKNOWN : money(priceCents)}</dd>
        </div>
        <div>
          <dt>Fees</dt>
          <dd>{money(feeTotalCents)}</dd>
        </div>
        <div>
          <dt>Net</dt>
          <dd>{netCents === null ? UNKNOWN : money(netCents)}</dd>
        </div>
        <div>
          <dt>Margin</dt>
          <dd>{margin === '' ? UNKNOWN : `${margin}%`}</dd>
        </div>
      </dl>
      <div className="row">
        <button disabled={saving} onClick={save} {...accel(KEYS.save)}>
          <AccessLabel
            text={saving ? 'Recording...' : 'Record sale'}
            accessKey={KEYS.save}
          />
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}
