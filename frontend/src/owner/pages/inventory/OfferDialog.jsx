import { useEffect, useRef, useState } from 'react'

import { AccessLabel } from '../../AccessLabel'
import { api } from '../../api'
import ModalDialog from '../../ModalDialog'
import { accel, useSaveShortcut } from '../../shortcuts'
import { isMoney } from '../orders/cents'
import { FORMATS, UNKNOWN } from '../listing-labels'
import {
  estimatedFees,
  hasDefaultFees,
  netAfterFees,
  netMarginPercent,
} from '../platform-rates'

/**
 * Offering items for sale: one platform, one format, a price per item.
 *
 * One platform for the whole batch because that is what the API takes
 * (`OfferIn`), and what the API takes is what the claim table can express:
 * an item is offered in one place at a time, so a batch that spanned
 * platforms would have to decide what a partial refusal means. The whole
 * batch is written or none of it is.
 *
 * Money is handled exactly as it arrives and exactly as it is typed: a
 * decimal string, never parsed into a JavaScript number, because a float
 * cannot hold cents. Amounts are shown without a currency code -- an
 * inventory row carries none, and `shared/format.js`'s `money()` would label
 * them USD whether or not that is true. Every calculation here goes through
 * `../platform-rates.js`.
 *
 * A refusal keeps the dialog open with every refused item named. The API
 * refuses a batch as a whole, so nothing was written and the prices already
 * typed are still the right ones to retry with.
 */

//: Alt+letter for the controls that are not per item. A per-item field
//: cannot carry one: an access key must be unique in the document and these
//: rows repeat. No letter is D, E or F -- Chrome and Edge keep those for the
//: address bar and menus on Windows -- and the test beside this file reads
//: them off the rendered dialog rather than from this table.
const KEYS = {
  venue: 'p',
  format: 'o',
  offer: 's',
}

/** A blank listing number is an absence, which the API spells `null`. */
const orNull = (text) => (text.trim() === '' ? null : text.trim())

/** What the form holds for one item before anything is typed. */
const draftFor = (item) => ({
  price: '',
  title: item.source_title ?? '',
  description: item.description ?? '',
  external_id: '',
})

export default function OfferDialog({ items, skipped = 0, onOffered, onClose }) {
  const [venues, setVenues] = useState([])
  const [venue, setVenue] = useState('')
  const [format, setFormat] = useState(FORMATS[0][0])
  const [rows, setRows] = useState(() =>
    Object.fromEntries(items.map((item) => [item.id, draftFor(item)])),
  )
  const [error, setError] = useState('')
  const [refused, setRefused] = useState([])
  const [offering, setOffering] = useState(false)

  // Guards offer()'s continuation once the request settles: Cancel (and
  // Escape, which ModalDialog routes to onClose) can unmount this dialog
  // while the batch is still in flight.
  //
  // The setup ARMS it; only the cleanup disarms it. The console runs in
  // StrictMode (`owner/main.jsx`), where React runs every effect setup,
  // cleanup, setup on mount: a ref only initialised at `useRef(true)` would
  // be left false by that first cleanup for the rest of the dialog's life,
  // and a batch the API accepted would never reach the parent.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .listSalesVenues()
      .then((found) => !cancelled && setVenues(found))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  // `offer` is a function declaration below, hoisted for the whole component
  // scope. Disabled while a batch is in flight, so holding Ctrl+S cannot send
  // a second one behind the first -- which, for offers, would be a second set
  // of listings rather than a harmless repeat.
  useSaveShortcut(offer, !offering)

  const chosen = venues.find((v) => v.code === venue) ?? null
  // The draft for one item. An item this dialog was handed after it opened
  // has none yet, and reading through `undefined` would take the whole editor
  // down; a blank draft instead has no price, so that item is named in the
  // refusal below rather than offered at some invented figure.
  const rowFor = (item) => rows[item.id] ?? draftFor(item)
  const set = (id, key) => (e) =>
    setRows((current) => ({
      ...current,
      [id]: { ...current[id], [key]: e.target.value },
    }))

  async function offer() {
    if (venue === '') {
      setError('Choose a platform to offer these on.')
      return
    }
    // Said here rather than left to the API, whose refusal for a blank price
    // is a schema complaint about a Decimal -- true, and no help to someone
    // who has not filled a row in yet.
    const unpriced = items.filter((item) => !isMoney(rowFor(item).price))
    if (unpriced.length > 0) {
      setError(
        `Price must be an amount like 189.00: ${unpriced
          .map((item) => item.item_code)
          .join(', ')}`,
      )
      return
    }
    setOffering(true)
    setError('')
    setRefused([])
    try {
      const batch = await api.createOffers({
        venue,
        format,
        items: items.map((item) => ({
          // A number, like `OfferItemIn.item_id`: Pydantic v2 does not
          // coerce "7" and answers 422.
          item_id: item.id,
          price: rowFor(item).price.trim(),
          title: rowFor(item).title.trim(),
          // Sent as typed, including empty: `description` is NOT NULL on the
          // row, and an empty string is how the wording is left blank.
          description: rowFor(item).description,
          external_id: orNull(rowFor(item).external_id),
        })),
      })
      if (!mounted.current) return
      onOffered(batch.listings)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
      // The per-item reasons, when the refusal has any. The race case is a
      // 409 with an empty list and a message that stands on its own, so this
      // must not insist on finding items to name.
      setRefused(err.body?.refused ?? [])
    } finally {
      if (mounted.current) setOffering(false)
    }
  }

  const label = `Offer ${items.length} item(s) for sale`
  const buttonText = offering ? 'Offering...' : `Offer ${items.length} for sale`

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      {refused.length > 0 && (
        <ul className="error">
          {refused.map((item) => (
            <li key={item.item_code}>
              {item.item_code}: {item.reason}
            </li>
          ))}
        </ul>
      )}
      {skipped > 0 && (
        <p className="muted">
          {skipped} other selected item(s) are not on this page, so they are not offered
          here.
        </p>
      )}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Platform" accessKey={KEYS.venue} />
          <select
            value={venue}
            onChange={(e) => setVenue(e.target.value)}
            {...accel(KEYS.venue)}
          >
            <option value="">Choose a platform</option>
            {venues.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <AccessLabel text="Format" accessKey={KEYS.format} />
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            {...accel(KEYS.format)}
          >
            {FORMATS.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Price</th>
            <th>Title</th>
            <th>Description</th>
            <th>Listing number</th>
            <th>Cost</th>
            <th>Fees</th>
            <th>Net</th>
            <th>Margin</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const draft = rowFor(item)
            const price = draft.price
            const fees = estimatedFees(price, chosen)
            const net = netAfterFees(price, chosen)
            const margin = netMarginPercent(price, item.total_cost, chosen)
            return (
              <tr key={item.id}>
                <td className="mono">{item.item_code}</td>
                <td>
                  {/* Text, not number: money crosses the API as a decimal
                      string and a number input hands back a float. */}
                  <input
                    inputMode="decimal"
                    aria-label={`Price for ${item.item_code}`}
                    value={price}
                    onChange={set(item.id, 'price')}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Title for ${item.item_code}`}
                    value={draft.title}
                    onChange={set(item.id, 'title')}
                  />
                </td>
                <td>
                  <textarea
                    rows={2}
                    aria-label={`Description for ${item.item_code}`}
                    value={draft.description}
                    onChange={set(item.id, 'description')}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Listing number for ${item.item_code}`}
                    value={draft.external_id}
                    onChange={set(item.id, 'external_id')}
                    placeholder="the platform's own number"
                  />
                </td>
                <td>{item.total_cost ?? UNKNOWN}</td>
                {/* Blank rather than zero when nobody has recorded what this
                    platform charges: "free" and "not looked up" are
                    different facts. */}
                <td>{fees === '' ? UNKNOWN : fees}</td>
                <td>{net === '' ? UNKNOWN : net}</td>
                <td>{margin === '' ? UNKNOWN : `${margin}%`}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {hasDefaultFees(chosen) && (
        <p className="muted">
          Fees are this platform&apos;s recorded defaults, not a quote from the
          platform.
        </p>
      )}
      <div className="row">
        <button disabled={offering} onClick={offer} {...accel(KEYS.offer)}>
          <AccessLabel text={buttonText} accessKey={KEYS.offer} />
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}
