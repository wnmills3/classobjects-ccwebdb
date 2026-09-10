import { useEffect, useState } from 'react'

import { api } from '../../api'

//: Outcome value the backend expects, paired with the button's label. The
//: backend spells the fourth one with a single L (`canceled`); the button
//: keeps the common spelling since that is what an operator reads.
const OUTCOMES = [
  ['received', 'Receive'],
  ['missing', 'Missing'],
  ['returned', 'Returned'],
  ['canceled', 'Cancelled'],
]

//: Today, as the operator's own calendar would show it -- built from local
//: getters rather than `toISOString()`, which reports UTC's date instead.
//: An arrival is a fact about the operator's "today," not UTC's: someone
//: opening a parcel at 11pm in a zone behind UTC is not opening it
//: "tomorrow" just because UTC has already turned over. See the comment on
//: the backend's `receive_items` for the other half of this pairing -- its
//: future-date bound is widened by a day precisely so this local default is
//: never itself refused.
function todayLocal() {
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * Records what arrived: one request per click, covering every selected item.
 *
 * All-or-nothing on the backend (`POST /api/inventory/receive`), so this
 * panel never turns a selection into a loop of per-item calls -- a box of
 * twenty coins is one transaction, and twenty requests would leave a partial
 * state nobody could describe if the tenth failed.
 *
 * On a refused request (409 already received, 422 future date, network) the
 * catch block only ever sets `error` -- every field the operator typed stays
 * exactly as they left it. Retyping a note after a rejected click is exactly
 * the friction that stops people writing notes at all.
 */
export default function ReceiptPanel({ itemIds, onDone }) {
  const [locations, setLocations] = useState([])
  const [arrivedOn, setArrivedOn] = useState(todayLocal)
  const [storageLocationId, setStorageLocationId] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listStorageLocations()
      .then((body) => {
        if (!cancelled) setLocations(body)
      })
      .catch(() => {
        /* the select just stays empty; a location is optional on a receipt */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const disabled = itemIds.length === 0 || busy

  async function submit(outcome) {
    setBusy(true)
    try {
      await api.receiveItems({
        item_ids: itemIds,
        outcome,
        arrived_on: arrivedOn || undefined,
        storage_location_id: storageLocationId ? Number(storageLocationId) : undefined,
        note: note || undefined,
      })
      setError('')
      setNote('')
      setStorageLocationId('')
      setArrivedOn(todayLocal())
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="receipt-panel">
      <div className="filter-grid">
        <label>
          Arrived
          <input
            type="date"
            value={arrivedOn}
            disabled={disabled}
            onChange={(e) => setArrivedOn(e.target.value)}
          />
        </label>

        <label>
          Storage location
          <select
            value={storageLocationId}
            disabled={disabled}
            onChange={(e) => setStorageLocationId(e.target.value)}
          >
            <option value="">--</option>
            {locations.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Note
          <input
            type="text"
            value={note}
            disabled={disabled}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      </div>

      <div className="receipt-actions">
        {OUTCOMES.map(([outcome, label]) => (
          <button key={outcome} disabled={disabled} onClick={() => submit(outcome)}>
            {label}
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  )
}
