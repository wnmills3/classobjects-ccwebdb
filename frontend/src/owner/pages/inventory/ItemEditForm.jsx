import { useEffect, useId, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'

/**
 * One item, every field, with what the lot claimed beside each.
 *
 * Two provenance marks appear on every field and they mean different things.
 * "lot says BU" is *derived* by comparing this piece to its parent: it says
 * what the seller claimed about the whole lot, and it recomputes so it cannot
 * drift. The confirm box is *asserted*: it says a person looked at this coin.
 * Neither is computable from the other, which is why both are here.
 */

const TEXT_FIELDS = [
  ['Title', 'source_title'],
  ['Description', 'description'],
]

const NUMBER_FIELDS = [['Pieces', 'piece_count']]

/**
 * Whether stored years are a range rather than one year.
 *
 * One year is stored as start == end, and a few older items as a start with
 * no end; both are one year. A range is for a multi-year set, or a coin dated
 * only to an era -- 13 items of 7,658 -- so the form asks for one year unless
 * the item already has a range. Opening a stored range as one would let a
 * save collapse it without anyone seeing the end year.
 */
function isRange(start, end) {
  return start != null && end != null && Number(start) !== Number(end)
}

/** An emptied number box clears the year rather than sending "". */
const yearValue = (text) => (text === '' ? null : text)

const MONEY_FIELDS = [
  ['Item cost', 'item_cost'],
  ['Shipping', 'shipping_cost'],
]

//: Form field -> the column a review record names. Only these can be
//: confirmed; the rest have no review box.
const REVIEWABLE = {
  year_start: 'year_start',
  year_end: 'year_end',
  piece_count: 'piece_count',
  grade: 'grade_id',
  denomination: 'denomination_id',
  country: 'country_id',
  metal: 'metal_id',
}

const CLASSIFIERS = [
  ['Grade', 'grade', 'grade'],
  ['Denomination', 'denomination', 'denomination'],
  ['Country', 'country', 'country'],
  ['Metal', 'metal', 'metal'],
]

export default function ItemEditForm({ itemId, onSaved, onClose }) {
  const [item, setItem] = useState(null)
  const [draft, setDraft] = useState({})
  const [reviewed, setReviewed] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [ranged, setRanged] = useState(false)
  const yearId = useId()
  const yearEndId = useId()

  useEffect(() => {
    let cancelled = false
    api
      .getInventoryItem(itemId)
      .then((body) => {
        if (cancelled) return
        setItem(body)
        setReviewed(body.reviewed ?? [])
        setRanged(isRange(body.year_start, body.year_end))
        setDraft({})
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [itemId])

  if (error && !item) return <p className="error">{error}</p>
  if (!item) return <p className="muted">Loading...</p>

  // `in`, not `??`: a year cleared in the draft is null, and must not fall
  // back to showing the stored year it is about to replace.
  const value = (key) => (key in draft ? draft[key] : item[key]) ?? ''
  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

  // One year is both ends. Sending both, rather than the start alone, is what
  // turns an item stored with a start and no end into one year as well.
  function setYear(e) {
    const year = yearValue(e.target.value)
    setDraft({ ...draft, year_start: year, year_end: year })
  }

  function toggleRange(e) {
    setRanged(e.target.checked)
    if (e.target.checked) return
    // Back to one year: the end follows the start. On an item that was one
    // year with no start edited, that is no change at all, so nothing is left
    // in the draft to save.
    const next = { ...draft }
    if (!isRange(item.year_start, item.year_end) && !('year_start' in draft)) {
      delete next.year_end
    } else {
      next.year_end = 'year_start' in draft ? draft.year_start : item.year_start
    }
    setDraft(next)
  }

  // A rate of zero means no tax was charged. Unticking goes back to the rate
  // the item was bought at, if it had one -- ticking and unticking is then no
  // change at all. An item recorded as untaxed has no rate of its own to go
  // back to, so it takes the configured one.
  const taxRate = draft.tax_rate ?? item.tax_rate
  const untaxed = taxRate !== undefined && Number(taxRate) === 0
  function toggleTax(e) {
    const next = { ...draft }
    if (e.target.checked) next.tax_rate = '0'
    else if (Number(item.tax_rate) !== 0) delete next.tax_rate
    else next.tax_rate = item.default_tax_rate
    setDraft(next)
  }

  // One column or several: one year is confirmed as both of its ends at once,
  // since a person looking at the coin confirmed the one year it shows.
  function toggleReview(columns) {
    const cols = [].concat(columns)
    const before = reviewed
    const next = cols.every((c) => reviewed.includes(c))
      ? reviewed.filter((c) => !cols.includes(c))
      : [...new Set([...reviewed, ...cols])]
    setReviewed(next)
    // replace:true, so unticking removes the record rather than leaving a
    // confirmation nobody stands behind any more.
    api.setItemReview(itemId, next, true).catch((err) => {
      // Put the box back. This mark is the record that a person examined the
      // coin, so a tick the server never accepted is worse than no tick.
      setReviewed(before)
      setError(err.message)
    })
  }

  function claimed(text) {
    // An empty cell rather than nothing: `.field` is a four-column grid, and a
    // missing child shifts everything after it into the wrong column.
    if (text === undefined || text === null) return <span />
    return (
      <span
        className="lot-claim"
        title={`The lot ${item.parent_item_code} claimed this`}
      >
        lot says {String(text)}
      </span>
    )
  }

  const claim = (column) => claimed(item.lot_claims?.[column])

  // The lot's years in the same shape as the one Year box: its range if it
  // claimed one, otherwise its year.
  function yearClaim() {
    const start = item.lot_claims?.year_start
    const end = item.lot_claims?.year_end
    return claimed(isRange(start, end) ? `${start}-${end}` : (start ?? end))
  }

  function review(columns) {
    if (!columns) return <span />
    const cols = [].concat(columns)
    return (
      <label className="review-mark" title="I have confirmed this by examination">
        <input
          type="checkbox"
          checked={cols.every((c) => reviewed.includes(c))}
          onChange={() => toggleReview(cols)}
        />
        {/* */}
        confirmed
      </label>
    )
  }

  const rangeToggle = (
    <label className="checkbox">
      <input type="checkbox" checked={ranged} onChange={toggleRange} />
      {/* */}
      Range of years
    </label>
  )

  async function save() {
    setSaving(true)
    try {
      // The version read when the form was opened. A save from a form loaded
      // before someone else's change is a 409, not a silent overwrite.
      await api.updateInventoryItem(itemId, { ...draft, version: item.version })
      setError('')
      onSaved?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>{item.item_code}</h2>
        {item.parent_item_code && (
          <span className="muted">split from {item.parent_item_code}</span>
        )}
        {onClose && (
          <button className="link" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {TEXT_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          <input type="text" value={value(key)} onChange={set(key)} />
          {claim(key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {/* Divs, not labels: a <label> may not contain the range checkbox's
          own label, so each box is named through htmlFor instead.

          One row that stays mounted, with the end year added beneath it.
          Two separate layouts replaced the checkbox itself on every tick, and
          a keyboard user's focus went with it. */}
      <div className="field">
        <label htmlFor={yearId}>{ranged ? 'Year from' : 'Year'}</label>
        <span className="year-input">
          <input
            id={yearId}
            type="number"
            value={value('year_start')}
            onChange={
              ranged
                ? (e) => setDraft({ ...draft, year_start: yearValue(e.target.value) })
                : setYear
            }
          />
          {rangeToggle}
        </span>
        {ranged ? claim('year_start') : yearClaim()}
        {review(ranged ? 'year_start' : ['year_start', 'year_end'])}
      </div>
      {ranged && (
        <div className="field">
          <label htmlFor={yearEndId}>Year to</label>
          <input
            id={yearEndId}
            type="number"
            // An item stored with a start and no end opens its range at the
            // start year -- but only until someone types here.
            value={
              'year_end' in draft
                ? (draft.year_end ?? '')
                : (item.year_end ?? item.year_start ?? '')
            }
            onChange={(e) =>
              setDraft({ ...draft, year_end: yearValue(e.target.value) })
            }
          />
          {claim('year_end')}
          {review('year_end')}
        </div>
      )}

      {NUMBER_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          <input type="number" value={value(key)} onChange={set(key)} />
          {claim(key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {MONEY_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          {/* Text, not number. Money crosses the API as a string and a number
              input would hand back a float, which is the one thing this
              schema is careful never to do. */}
          <input
            type="text"
            inputMode="decimal"
            value={value(key)}
            onChange={set(key)}
          />
          {/* No lot ever claims a cost -- a piece's cost is allocated at
              split time, not inherited -- but `.field` is a four-column grid
              and every other row fills this slot, so an empty one is called
              for explicitly rather than left to shift the review box into
              its neighbour's column. */}
          {claim(key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {item.tax_rate !== undefined && (
        <div className="field">
          Sales tax
          <span title="Recalculated by the database when saved">
            {`$${item.sales_tax}`}
          </span>
          <label className="checkbox">
            <input type="checkbox" checked={untaxed} onChange={toggleTax} />
            {/* */}
            No sales tax charged
          </label>
          <span />
        </div>
      )}

      {CLASSIFIERS.map(([label, key, table]) => (
        <label key={key} className="field">
          {label}
          <ReferenceSelect
            table={table}
            value={value(key)}
            onChange={set(key)}
            // Paper money is graded on its own scale: a note is offered only
            // note grades, and anything else only the coin scales.
            filter={
              key === 'grade'
                ? (grade) =>
                    (grade.extra?.grade_scale === 'note') ===
                    (value('item_kind') === 'currency')
                : undefined
            }
          />
          {claim(key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      <div className="row">
        <button disabled={saving || Object.keys(draft).length === 0} onClick={save}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
