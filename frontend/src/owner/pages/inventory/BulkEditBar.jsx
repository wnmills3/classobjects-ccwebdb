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
 *
 * A selection holding items that are up for sale is refused, naming them; a
 * box then appears to change them anyway.
 */

//: The refusal the server gives a change to items that are for sale.
const FOR_SALE = 'For sale'

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
  // Shown once the server has said some of the selection is for sale.
  const [forSale, setForSale] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)

  if (ids.length === 0) return null

  const type = BULK_FIELDS.find(([, key]) => key === field)?.[2] ?? 'text'

  async function apply() {
    setBusy(true)
    try {
      const changes = { [field]: type === 'number' ? Number(value) : value }
      if (acknowledged) changes.acknowledge_for_sale = true
      await api.bulkEditInventory(ids, changes)
      setError('')
      setValue('')
      setForSale(false)
      setAcknowledged(false)
      onApplied?.()
    } catch (err) {
      setError(err.message)
      if (err.message.startsWith(FOR_SALE)) setForSale(true)
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

      {forSale && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          {/* */}
          Change the items for sale too
        </label>
      )}
      {error && <span className="error">{error}</span>}
    </div>
  )
}
