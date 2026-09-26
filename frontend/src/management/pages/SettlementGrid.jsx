import { useState } from 'react'

import { api } from '../api'
import ConfirmDialog from '../ConfirmDialog'
import { RESULTS } from './auction-labels'
import { fromCents, isMoney, toCents } from '../../shared/cents'
import { subjectOf, UNKNOWN } from './listing-labels'
import { useReference } from '../../shared/reference-context'
import { useMounted } from '../useMounted'

/**
 * The settlement grid: every lot's outcome, every buyer's fees, applied to a
 * closed auction in one transaction.
 *
 * `POST /api/auctions/{id}/settle` (`app.auctions.settle`) is all or
 * nothing -- one savepoint over every buyer's order -- so this grid is
 * filled in and sent whole, the same shape `RecordSaleDialog.jsx` sends a
 * single sale in. Money crosses this component exactly as it is typed and
 * exactly as it is sent: a decimal string, never rounded through a
 * JavaScript number. The only arithmetic here -- gross, fees, net and cost
 * basis -- is done in whole cents via `shared/cents.js`, for the totals
 * shown on screen, and none of it is what gets sent.
 *
 * **A lot with no result chosen is simply left out of `lines`.** That is not
 * an oversight: `app.auctions.settle` reports "lot N has no result" for
 * exactly that shape, structured per lot (`AuctionRefusal.lot_number`,
 * ruling R21), and this component's whole reason for filling `refused` into
 * a per-row map is to show that refusal on the row it is about rather than
 * as one sentence the owner has to match back to the grid by hand.
 */

/** Whole cents as a decimal string, negative ones included -- a settlement
 * can net less than zero once fees are counted. */
function money(cents) {
  return cents < 0 ? `-${fromCents(-cents)}` : fromCents(cents)
}

/** A typed amount, in cents, or zero when it is blank or not an amount. */
function centsOf(text) {
  const trimmed = String(text ?? '').trim()
  return isMoney(trimmed) ? toCents(trimmed) : 0
}

/** What the grid holds for one lot before anything is typed. */
const EMPTY_LINE = { result: '', hammer_price: '', buyer_username: '' }

/**
 * The question asked before a settlement is applied.
 *
 * The grid itself is the thing to review -- every lot's result, every
 * buyer's fees, the totals below -- so this dialog does not repeat any of
 * it; it only says what "Settle" is about to do and that a refused grid
 * writes nothing.
 */
function SettleConfirm({ auction, busy, canSettle, onConfirm, onCancel }) {
  return (
    <ConfirmDialog
      question={`Settle ${auction.title}?`}
      confirmLabel="Settle"
      busyLabel="Settling..."
      cancelLabel="Keep reviewing"
      busy={busy}
      disabled={!canSettle}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p>
        Every lot&apos;s result and hammer price, and every buyer&apos;s fees, are
        written at once -- one order per buyer. If any lot in the grid has a problem,
        nothing is written; the grid stays open with every offending lot marked.
      </p>
    </ConfirmDialog>
  )
}

export default function SettlementGrid({
  auction,
  isAuctionHouse,
  locations,
  onSettled,
}) {
  const [lines, setLines] = useState({})
  // One typed amount per buyer key and fee kind code. A buyer key of '' is
  // the auction house's standing undisclosed buyer -- the same meaning
  // `SettlementFeesIn.buyer_username=None` carries.
  const [feeAmounts, setFeeAmounts] = useState({})
  const [returnedToLocationId, setReturnedToLocationId] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [settling, setSettling] = useState(false)
  const [error, setError] = useState('')
  const [refused, setRefused] = useState([])
  const feeKinds = useReference('sales_fee_kind') ?? []

  // The same guard every other console form carries: Cancel or Escape can
  // close the confirmation while the settle request is still in flight, and
  // the grid can be swapped out from under this component the moment
  // `onSettled` moves the auction off `closed` -- see the module docstring.
  const mounted = useMounted()

  const lineFor = (lot) => lines[lot.id] ?? EMPTY_LINE
  const setLine = (lot, field, value) =>
    setLines((current) => ({
      ...current,
      [lot.id]: { ...(current[lot.id] ?? EMPTY_LINE), [field]: value },
    }))

  // Every buyer named on a lot currently marked sold, grouped the way
  // `app.auctions._buyer_key` groups them server-side: casefolded, so
  // `amy` on one lot and `Amy` on another are one buyer here too, not two
  // fee sub-tables the owner has no way to reconcile. `key` is the fold,
  // used for lookups and React keys; `raw` is the **first spelling seen**,
  // in `auction.lots`' own ascending-id order -- the same "first spelling
  // wins" rule `_BuyerGroup.username` uses for the customer record, so the
  // spelling this sends back as `buyer_username` is the same one the
  // backend would have kept had two spellings reached it. A blank buyer is
  // only offered a fee row for an auction house, where blank is the
  // standing undisclosed buyer rather than a form nobody finished filling
  // in.
  const buyersByKey = new Map()
  for (const lot of auction.lots) {
    const line = lineFor(lot)
    if (line.result !== 'sold') continue
    const raw = line.buyer_username.trim()
    if (raw === '' && !isAuctionHouse) continue
    const key = raw.toLowerCase()
    if (!buyersByKey.has(key)) {
      buyersByKey.set(key, { key, raw, label: raw === '' ? 'Undisclosed buyer' : raw })
    }
  }
  const buyers = Array.from(buyersByKey.values())

  const setFee = (buyerKey, kindCode) => (e) =>
    setFeeAmounts((current) => ({
      ...current,
      [buyerKey]: { ...(current[buyerKey] ?? {}), [kindCode]: e.target.value },
    }))

  // Refusals (ruling R21), split by whether they name one lot in particular.
  const problemsByLot = {}
  const generalProblems = []
  for (const row of refused) {
    if (row.lot_number) {
      problemsByLot[row.lot_number] = [
        ...(problemsByLot[row.lot_number] ?? []),
        row.reason,
      ]
    } else {
      generalProblems.push(row.reason)
    }
  }

  const grossCents = auction.lots.reduce((sum, lot) => {
    const line = lineFor(lot)
    return line.result === 'sold' ? sum + centsOf(line.hammer_price) : sum
  }, 0)
  const feesCents = Object.values(feeAmounts).reduce(
    (sum, kinds) => sum + Object.values(kinds).reduce((s, v) => s + centsOf(v), 0),
    0,
  )
  const netCents = grossCents - feesCents
  const costBasisCents = auction.lots.reduce(
    (sum, lot) => sum + centsOf(lot.listing.cost_basis),
    0,
  )

  // `app.auctions.settle` refuses when the house still holds something
  // (`auction.consigned_on is not None`) and at least one lot with a
  // chosen, non-sold result has nowhere named to come back to
  // (`_grid_problems`'s own `coming_home` check). `RemoveLotConfirm` and
  // `CancelConfirm` in `Auctions.jsx` disable their confirm button the same
  // way, on the same `consigned_on` predicate -- this is that pattern
  // applied here, so the owner cannot walk into a refusal the console could
  // see coming.
  const needsReturnLocation =
    auction.consigned_on != null &&
    auction.lots.some((lot) => {
      const result = lineFor(lot).result
      return result !== '' && result !== 'sold'
    })
  const canSettle = !needsReturnLocation || returnedToLocationId !== ''

  async function settle() {
    setSettling(true)
    setError('')
    setRefused([])
    const settlementLines = []
    for (const lot of auction.lots) {
      const line = lineFor(lot)
      // A lot with no result chosen is simply omitted -- `app.auctions.settle`
      // reports "lot N has no result" for exactly that shape, and that
      // per-lot refusal is what marks this row rather than a client-side
      // guess at the same message.
      if (line.result === '') continue
      const sold = line.result === 'sold'
      settlementLines.push({
        auction_lot_id: lot.id,
        result: line.result,
        hammer_price:
          sold && line.hammer_price.trim() !== '' ? line.hammer_price.trim() : null,
        buyer_username:
          sold && line.buyer_username.trim() !== '' ? line.buyer_username.trim() : null,
      })
    }
    const fees = buyers
      .map((buyer) => ({
        buyer_username: buyer.key === '' ? null : buyer.raw,
        fees: feeKinds
          .map((kind) => ({
            kind: kind.code,
            text: String(feeAmounts[buyer.key]?.[kind.code] ?? '').trim(),
          }))
          .filter(({ text }) => text !== '')
          .map(({ kind, text }) => ({ kind, amount: text })),
      }))
      .filter((group) => group.fees.length > 0)
    const payload = { lines: settlementLines, fees }
    if (returnedToLocationId !== '') {
      payload.returned_to_location_id = Number(returnedToLocationId)
    }
    try {
      const result = await api.settleAuction(auction.id, payload)
      if (!mounted.current) return
      setConfirming(false)
      onSettled(result)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
      setRefused(err.body?.refused ?? [])
      setConfirming(false)
    } finally {
      if (mounted.current) setSettling(false)
    }
  }

  return (
    <section className="settlement-grid">
      <h3>Settle this auction</h3>
      {error && <p className="error">{error}</p>}
      {generalProblems.length > 0 && (
        <ul className="error">
          {generalProblems.map((reason) => (
            // `reason` is the key: refusal text has no id of its own, and
            // two problems are never worded identically in one settlement.
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>Lot</th>
            <th>Item</th>
            <th>Reserve</th>
            <th>Result</th>
            <th>Hammer price</th>
            <th>Buyer</th>
            <th>Problem</th>
          </tr>
        </thead>
        <tbody>
          {auction.lots.map((lot) => {
            const line = lineFor(lot)
            const problem = problemsByLot[lot.lot_number]
            return (
              <tr key={lot.id} className={problem ? 'error' : ''}>
                <td className="mono">{lot.lot_number}</td>
                <td>{subjectOf(lot.listing)}</td>
                <td>{lot.reserve ?? UNKNOWN}</td>
                <td>
                  <select
                    aria-label={`Result for lot ${lot.lot_number}`}
                    value={line.result}
                    onChange={(e) => setLine(lot, 'result', e.target.value)}
                  >
                    <option value="">Choose a result</option>
                    {RESULTS.map(([value, text]) => (
                      <option key={value} value={value}>
                        {text}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    inputMode="decimal"
                    aria-label={`Hammer price for lot ${lot.lot_number}`}
                    value={line.hammer_price}
                    disabled={line.result !== 'sold'}
                    onChange={(e) => setLine(lot, 'hammer_price', e.target.value)}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Buyer for lot ${lot.lot_number}`}
                    value={line.buyer_username}
                    disabled={line.result !== 'sold'}
                    placeholder={isAuctionHouse ? 'blank = undisclosed buyer' : ''}
                    onChange={(e) => setLine(lot, 'buyer_username', e.target.value)}
                  />
                </td>
                <td className="error">{problem ? problem.join('; ') : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <h4>Fees</h4>
      {buyers.length === 0 && <p className="muted">No lot is marked sold yet.</p>}
      {buyers.map((buyer) => (
        <fieldset key={buyer.key}>
          <legend>{buyer.label}</legend>
          <table>
            <tbody>
              {feeKinds.map((kind) => (
                <tr key={kind.code}>
                  <td>{kind.label}</td>
                  <td>
                    <input
                      inputMode="decimal"
                      aria-label={`${kind.label} fee for ${buyer.label}`}
                      value={feeAmounts[buyer.key]?.[kind.code] ?? ''}
                      onChange={setFee(buyer.key, kind.code)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </fieldset>
      ))}

      {isAuctionHouse && (
        <label>
          Return unsold and withdrawn items to
          <select
            aria-label="Return unsold and withdrawn items to"
            value={returnedToLocationId}
            onChange={(e) => setReturnedToLocationId(e.target.value)}
          >
            <option value="">Choose a location</option>
            {locations.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <dl className="summary">
        <div>
          <dt>Gross</dt>
          <dd>{money(grossCents)}</dd>
        </div>
        <div>
          <dt>Fees</dt>
          <dd>{money(feesCents)}</dd>
        </div>
        <div>
          <dt>Net</dt>
          <dd>{money(netCents)}</dd>
        </div>
        <div>
          <dt>Cost basis</dt>
          <dd>{money(costBasisCents)}</dd>
        </div>
      </dl>

      <div className="row">
        <button disabled={!canSettle} onClick={() => setConfirming(true)}>
          Settle...
        </button>
        {!canSettle && (
          <span className="muted">
            Choose where unsold and withdrawn items come back to first.
          </span>
        )}
      </div>

      {confirming && (
        <SettleConfirm
          auction={auction}
          busy={settling}
          canSettle={canSettle}
          onConfirm={settle}
          onCancel={() => setConfirming(false)}
        />
      )}
    </section>
  )
}
