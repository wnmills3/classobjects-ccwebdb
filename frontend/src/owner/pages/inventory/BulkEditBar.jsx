import { useState } from 'react'

import { api } from '../../api'

/**
 * Set one field across a selection.
 *
 * One field at a time on purpose. The bulk case is "these twenty are all
 * 1964" -- a form offering every field at once invites setting four of them
 * across twenty coins from one glance at one coin.
 *
 * The server applies it in a single transaction, so a rejected edit changes
 * nothing and there is never a half-applied selection to report.
 */

const BULK_FIELDS = [
  ['Year', 'year_start', 'number'],
  ['Grade', 'grade', 'text'],
  ['Country', 'country', 'text'],
  ['Denomination', 'denomination', 'text'],
  ['Metal', 'metal', 'text'],
]

export default function BulkEditBar({ ids, onApplied, onClear }) {
  const [field, setField] = useState(BULK_FIELDS[0][1])
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (ids.length === 0) return null

  const type = BULK_FIELDS.find(([, key]) => key === field)?.[2] ?? 'text'

  async function apply() {
    setBusy(true)
    try {
      await api.bulkEditInventory(ids, {
        [field]: type === 'number' ? Number(value) : value,
      })
      setError('')
      setValue('')
      onApplied?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bulk-bar">
      <strong>{ids.length} selected</strong>

      <select value={field} onChange={(e) => setField(e.target.value)}>
        {BULK_FIELDS.map(([label, key]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>

      <input
        type={type}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="New value"
      />

      <button disabled={busy || value === ''} onClick={apply}>
        {busy ? 'Applying...' : `Apply to ${ids.length}`}
      </button>
      <button className="link" onClick={onClear}>
        Clear selection
      </button>

      {error && <span className="error">{error}</span>}
    </div>
  )
}
