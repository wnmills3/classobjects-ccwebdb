import { useState } from 'react'

import ItemEditForm from './ItemEditForm'

/**
 * Walk a result set one item at a time.
 *
 * **The queue is frozen at entry.** `ids` is captured once, when review
 * starts, and never re-read. If it re-ran the search at each step, fixing
 * item 3's missing year would remove it from `issue=no_year`, the set would
 * shrink to 22, and every position after it would shift -- silently skipping
 * an item. Fixed items stay in the list, marked done.
 */
export default function ReviewPane({ ids, onClose }) {
  const [at, setAt] = useState(0)
  const [done, setDone] = useState([])

  if (ids.length === 0) return null
  const itemId = ids[at]

  return (
    <div className="review-pane">
      <div className="row">
        <button disabled={at === 0} onClick={() => setAt(at - 1)}>
          Previous
        </button>
        <span className="muted">
          {at + 1} of {ids.length}
          {done.length > 0 && ` -- ${done.length} saved`}
        </span>
        <button disabled={at + 1 >= ids.length} onClick={() => setAt(at + 1)}>
          Next
        </button>
        <button className="link" onClick={onClose}>
          Leave review
        </button>
      </div>

      <ItemEditForm
        key={itemId}
        itemId={itemId}
        onSaved={() => {
          setDone((d) => (d.includes(itemId) ? d : [...d, itemId]))
          // Saving advances, because the next thing you want after finishing
          // a coin is the next coin.
          if (at + 1 < ids.length) setAt(at + 1)
        }}
      />
    </div>
  )
}
