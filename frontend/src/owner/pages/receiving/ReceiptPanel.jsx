import { useEffect, useState } from 'react'

import { api } from '../../api'
import ReviewPane from '../inventory/ReviewPane'

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
  const [photos, setPhotos] = useState([])
  const [photoInputKey, setPhotoInputKey] = useState(0)
  const [uploadError, setUploadError] = useState('')
  const [reviewOpen, setReviewOpen] = useState(false)

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

      // The arrival above is the fact; a photograph is evidence added to it
      // afterwards. A failed upload must not undo the receipt just recorded,
      // so it is caught on its own -- reported against the item, retryable,
      // and never allowed to roll the receipt back or skip `onDone`.
      if (photos.length > 0) {
        try {
          await Promise.all(
            itemIds.flatMap((id) =>
              photos.map((file, index) =>
                api.uploadImage(id, file, { isPrimary: index === 0 }),
              ),
            ),
          )
          setPhotos([])
          setPhotoInputKey((key) => key + 1)
          setUploadError('')
        } catch (err) {
          setUploadError(err.message)
        }
      }
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

        <label>
          Photo
          <input
            key={photoInputKey}
            type="file"
            accept="image/*"
            multiple
            disabled={disabled}
            onChange={(e) => setPhotos(Array.from(e.target.files ?? []))}
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
      {uploadError && <p className="error">{uploadError}</p>}

      {/* Collapsed by default -- expanding shows the item's reviewable
          fields, composed from the same ReviewPane/ItemEditForm the
          inventory page uses, so this never drifts from that page's idea of
          what a reviewable field is. */}
      <div className="review-toggle">
        {!reviewOpen && (
          <button type="button" onClick={() => setReviewOpen(true)}>
            Confirm or correct fields
          </button>
        )}
        {reviewOpen && (
          <ReviewPane ids={itemIds} onClose={() => setReviewOpen(false)} />
        )}
      </div>
    </div>
  )
}
