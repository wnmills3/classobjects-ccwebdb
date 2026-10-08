import { useState } from 'react'

import { api } from '../api'

const INVENTORY_VIEWS = ['coins', 'currency']

/**
 * Finds one inventory item by code, searching both views since an item code
 * alone does not say whether it is a coin or a piece of currency.
 *
 * `onPick` receives the chosen row (`{ id, item_code, description }`) or
 * `null` when the operator edits the code after having picked one -- a
 * stale selection pointing at a different item than the box now on screen
 * is worse than no selection at all.
 */
export default function ItemPicker({ onPick }) {
  const [code, setCode] = useState('')
  const [matches, setMatches] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function changeCode(value) {
    setCode(value)
    setMatches(null)
    onPick(null)
  }

  async function find() {
    const wanted = code.trim()
    if (!wanted) return
    setBusy(true)
    setError('')
    try {
      const pages = await Promise.all(
        INVENTORY_VIEWS.map((view) => api.searchInventory(view, { item_code: wanted })),
      )
      const rows = pages.flatMap((page) => page.rows)
      setMatches(rows)
      if (rows.length === 0) setError('No item matches that code.')
    } catch (err) {
      setError(err.message)
      setMatches(null)
    } finally {
      setBusy(false)
    }
  }

  function pick(row) {
    setMatches(null)
    setError('')
    setCode(row.item_code)
    onPick(row)
  }

  return (
    <div className="item-picker">
      <label>
        Item code{/* */}
        <input
          type="text"
          value={code}
          onChange={(e) => changeCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              find()
            }
          }}
        />
      </label>
      <button type="button" disabled={busy} onClick={find}>
        Find
      </button>
      {error && <p className="error">{error}</p>}
      {matches && matches.length > 0 && (
        <ul className="item-picker-matches">
          {matches.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                className="item-picker-row"
                onClick={() => pick(row)}
              >
                <span className="mono">{row.item_code}</span> {row.description}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
