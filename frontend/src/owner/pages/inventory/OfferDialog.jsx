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
 * **Or one assembled lot**, which is one thing however many coins are in it.
 * `OfferIn` takes exactly one subject -- a list of `items` or a `lot_id` --
 * and a lot body carries one price, title, description and listing number at
 * the top level rather than a row per coin. This dialog is the same dialog
 * for both because the decision being made is the same one: which platform,
 * which format, what price. It shows one row for the lot, priced as a whole,
 * because that is what is being sold; the coins inside it are the Lots page's
 * business, not this dialog's.
 *
 * `quantity` is never sent for a lot: `ck_listing_lot_quantity_one` caps a lot
 * listing at one unit, and `OfferIn` answers 422 rather than quietly ignoring
 * a second one.
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

/**
 * What is being offered, as one row each, whichever shape it arrived in.
 *
 * An inventory row and a lot carry their wording under different names --
 * `source_title` against `title`, `total_cost` against `cost_basis` -- and
 * a lot is one row however many coins it holds. Reducing both to this shape
 * here is what lets the form, the pricing and the refusal below be written
 * once. `key` is what the draft table is keyed by, and is prefixed for a lot
 * so a lot id can never collide with an item id.
 */
function subjectsFor(items, lot) {
  if (lot) {
    return [
      {
        key: `lot-${lot.id}`,
        label: lot.title,
        title: lot.title,
        description: lot.description ?? '',
        cost: lot.cost_basis,
      },
    ]
  }
  return items.map((item) => ({
    key: String(item.id),
    itemId: item.id,
    label: item.item_code,
    title: item.source_title ?? '',
    description: item.description ?? '',
    cost: item.total_cost,
  }))
}

/**
 * What the form holds for one subject before anything is typed.
 *
 * `startedAs` is the title the row opened with, kept so a suggested title
 * that arrives later replaces only a title nobody has edited. It is never
 * sent: the request body is built field by field below.
 */
const draftFor = (subject) => ({
  price: '',
  title: subject.title,
  startedAs: subject.title,
  description: subject.description,
  external_id: '',
})

export default function OfferDialog({
  items = [],
  lot = null,
  skipped = 0,
  onOffered,
  onClose,
}) {
  const [venues, setVenues] = useState([])
  const [venue, setVenue] = useState('')
  const [format, setFormat] = useState(FORMATS[0][0])
  const [rows, setRows] = useState(() =>
    Object.fromEntries(
      subjectsFor(items, lot).map((subject) => [subject.key, draftFor(subject)]),
    ),
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

  // The public title each item starts from, composed from what the record
  // says it is (`app/offer_titles.py`) -- not `source_title`, the seller's
  // wording from the purchase, which is often "1" or "$1 Bill" and would
  // otherwise go onto eBay as typed. Only a title nobody has touched yet is
  // replaced: the answer can land after the operator started typing. A lot
  // keeps its own title, which the owner wrote when assembling it.
  const [titlesFailed, setTitlesFailed] = useState(false)
  const itemIds = lot ? '' : items.map((item) => item.id).join(',')
  useEffect(() => {
    if (itemIds === '') return undefined
    let cancelled = false
    api
      .getOfferTitles(itemIds.split(',').map(Number))
      .then(({ titles }) => {
        if (cancelled) return
        setRows((current) => {
          const next = { ...current }
          Object.entries(titles).forEach(([id, title]) => {
            const draft = next[id]
            if (draft && draft.title === draft.startedAs) {
              next[id] = { ...draft, title }
            }
          })
          return next
        })
      })
      .catch(() => !cancelled && setTitlesFailed(true))
    return () => {
      cancelled = true
    }
  }, [itemIds])

  // `offer` is a function declaration below, hoisted for the whole component
  // scope. Disabled while a batch is in flight, so holding Ctrl+S cannot send
  // a second one behind the first -- which, for offers, would be a second set
  // of listings rather than a harmless repeat.
  useSaveShortcut(offer, !offering)

  const chosen = venues.find((v) => v.code === venue) ?? null
  const subjects = subjectsFor(items, lot)
  // The draft for one subject. An item this dialog was handed after it opened
  // has none yet, and reading through `undefined` would take the whole editor
  // down; a blank draft instead has no price, so that item is named in the
  // refusal below rather than offered at some invented figure.
  const rowFor = (subject) => rows[subject.key] ?? draftFor(subject)
  const set = (subject, key) => (e) =>
    setRows((current) => ({
      ...current,
      // Read out of `current`, not out of the render's `rows`: two keystrokes
      // batched into one update would otherwise both start from the older
      // draft and the first of them would be lost.
      [subject.key]: {
        ...(current[subject.key] ?? draftFor(subject)),
        [key]: e.target.value,
      },
    }))

  async function offer() {
    if (venue === '') {
      setError('Choose a platform to offer these on.')
      return
    }
    // Said here rather than left to the API, whose refusal for a blank price
    // is a schema complaint about a Decimal -- true, and no help to someone
    // who has not filled a row in yet.
    const unpriced = subjects.filter((subject) => !isMoney(rowFor(subject).price))
    if (unpriced.length > 0) {
      setError(
        `Price must be an amount like 189.00: ${unpriced
          .map((subject) => subject.label)
          .join(', ')}`,
      )
      return
    }
    setOffering(true)
    setError('')
    setRefused([])
    try {
      // Exactly one subject, the way `OfferIn._one_subject` requires it. A
      // lot body carries no `items` key at all rather than an empty list:
      // the schema forbids the pair, and an empty list happens to slip past
      // that check today only because it is falsy.
      const batch = await api.createOffers(
        lot
          ? {
              venue,
              format,
              // A number, like `OfferIn.lot_id`: Pydantic v2 does not
              // coerce "7" and answers 422.
              lot_id: lot.id,
              price: rowFor(subjects[0]).price.trim(),
              title: rowFor(subjects[0]).title.trim(),
              description: rowFor(subjects[0]).description,
              external_id: orNull(rowFor(subjects[0]).external_id),
            }
          : {
              venue,
              format,
              items: subjects.map((subject) => {
                const draft = rowFor(subject)
                return {
                  // A number, like `OfferItemIn.item_id`: Pydantic v2 does
                  // not coerce "7" and answers 422.
                  item_id: subject.itemId,
                  price: draft.price.trim(),
                  title: draft.title.trim(),
                  // Sent as typed, including empty: `description` is NOT
                  // NULL on the row, and an empty string is how the wording
                  // is left blank.
                  description: draft.description,
                  external_id: orNull(draft.external_id),
                }
              }),
            },
      )
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

  // A lot is named rather than counted: "Offer 1 item(s) for sale" would be
  // true of a three-coin lot and would read as a single coin.
  const label = lot
    ? `Offer the lot ${lot.title} for sale`
    : `Offer ${items.length} item(s) for sale`
  const action = lot ? 'Offer the lot for sale' : `Offer ${items.length} for sale`
  const buttonText = offering ? 'Offering...' : action

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
      {titlesFailed && (
        <p className="muted">
          Suggested titles could not be loaded, so each title is the wording from the
          purchase. Check it before offering.
        </p>
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
            <th>{lot ? 'Lot' : 'Item'}</th>
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
          {subjects.map((subject) => {
            const draft = rowFor(subject)
            const price = draft.price
            const fees = estimatedFees(price, chosen)
            const net = netAfterFees(price, chosen)
            const margin = netMarginPercent(price, subject.cost, chosen)
            return (
              <tr key={subject.key}>
                <td className="mono">{subject.label}</td>
                <td>
                  {/* Text, not number: money crosses the API as a decimal
                      string and a number input hands back a float. */}
                  <input
                    inputMode="decimal"
                    aria-label={`Price for ${subject.label}`}
                    value={price}
                    onChange={set(subject, 'price')}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Title for ${subject.label}`}
                    value={draft.title}
                    onChange={set(subject, 'title')}
                  />
                </td>
                <td>
                  <textarea
                    rows={2}
                    aria-label={`Description for ${subject.label}`}
                    value={draft.description}
                    onChange={set(subject, 'description')}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Listing number for ${subject.label}`}
                    value={draft.external_id}
                    onChange={set(subject, 'external_id')}
                    placeholder="the platform's own number"
                  />
                </td>
                <td>{subject.cost ?? UNKNOWN}</td>
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
