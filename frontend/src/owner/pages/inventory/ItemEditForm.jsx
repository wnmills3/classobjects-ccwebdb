import { useEffect, useState } from 'react'

import { api } from '../../../shared/api'
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

const NUMBER_FIELDS = [
  ['Year from', 'year_start'],
  ['Year to', 'year_end'],
  ['Pieces', 'piece_count'],
]

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

  useEffect(() => {
    let cancelled = false
    api
      .getInventoryItem(itemId)
      .then((body) => {
        if (cancelled) return
        setItem(body)
        setReviewed(body.reviewed ?? [])
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

  const value = (key) => draft[key] ?? item[key] ?? ''
  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

  function toggleReview(column) {
    const before = reviewed
    const next = reviewed.includes(column)
      ? reviewed.filter((c) => c !== column)
      : [...reviewed, column]
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

  function claim(column) {
    const claimed = item.lot_claims?.[column]
    // An empty cell rather than nothing: `.field` is a four-column grid, and a
    // missing child shifts everything after it into the wrong column.
    if (claimed === undefined || claimed === null) return <span />
    return (
      <span
        className="lot-claim"
        title={`The lot ${item.parent_item_code} claimed this`}
      >
        lot says {String(claimed)}
      </span>
    )
  }

  function review(column) {
    if (!column) return <span />
    return (
      <label className="review-mark" title="I have confirmed this by examination">
        <input
          type="checkbox"
          checked={reviewed.includes(column)}
          onChange={() => toggleReview(column)}
        />
        {/* */}
        confirmed
      </label>
    )
  }

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

      {CLASSIFIERS.map(([label, key, table]) => (
        <label key={key} className="field">
          {label}
          <ReferenceSelect table={table} value={value(key)} onChange={set(key)} />
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
