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
  // The ids under review, frozen at the moment review was opened -- null
  // means review is closed. `ReviewPane` never re-reads its `ids` prop (see
  // its own docstring), so handing it the live `itemIds` prop directly would
  // let a later tick/untick on this same panel shrink or reorder the queue
  // out from under an index `ReviewPane` never clamps. Snapshotting here,
  // once, is what "frozen" actually requires.
  const [reviewIds, setReviewIds] = useState(null)

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
  // A photograph is evidence of one physical object. With several items
  // selected there is no honest way to say which of them the picture is of
  // -- attaching it to all of them (or guessing "the first") would put a
  // wrong provenance record on every item but one, and a wrong record reads
  // as a right one. So the control itself refuses the ambiguous case rather
  // than the submit path having to un-guess it later.
  const singleItemSelected = itemIds.length === 1
  const photoDisabled = disabled || !singleItemSelected
  // Named so the render below can tell the operator exactly what is about to
  // be silently dropped, rather than only that photos and multiple items
  // don't mix.
  const pendingPhotoNames = photos.map((file) => file.name)

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
      setUploadError('')
      setNote('')
      setStorageLocationId('')
      setArrivedOn(todayLocal())
      // The section only ever has something to review while an item is
      // selected; once the receipt clears the selection, `ReviewPane` would
      // render nothing for an empty queue anyway, but leaving `reviewIds` set
      // would still keep the "Confirm or correct fields" button hidden
      // behind a review pane nobody can see. Closing it here is what makes
      // it reappear for the next item.
      setReviewIds(null)
      onDone?.()

      // The arrival above is the fact; a photograph is evidence added to it
      // afterwards. A failed upload must not undo the receipt just recorded,
      // so it is caught on its own -- reported against the item, retryable,
      // and never allowed to roll the receipt back or skip `onDone`. Only
      // ever reachable with exactly one item: the input above is disabled
      // otherwise, and this check is the same guarantee enforced again at
      // the point that actually names the item to attach to.
      if (photos.length > 0 && singleItemSelected) {
        const [itemId] = itemIds
        const results = await Promise.allSettled(
          photos.map((file, index) =>
            api.uploadImage(itemId, file, { isPrimary: index === 0 }),
          ),
        )
        // allSettled, not all: one bad file must not hide whether the other
        // photos in the same batch landed. Reported by name, since "it
        // failed" without saying which file is not something the operator
        // can act on.
        const failures = results
          .map((result, index) => [result, photos[index]])
          .filter(([result]) => result.status === 'rejected')
        setUploadError(
          failures
            .map(([result, file]) => `${file.name}: ${result.reason.message}`)
            .join('; '),
        )
        setPhotos([])
        setPhotoInputKey((key) => key + 1)
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
            disabled={photoDisabled}
            onChange={(e) => setPhotos(Array.from(e.target.files ?? []))}
          />
          {itemIds.length > 1 && pendingPhotoNames.length === 0 && (
            <span className="muted">
              Photographs attach to a single item -- receive this one on its own to add
              one.
            </span>
          )}
          {itemIds.length > 1 && pendingPhotoNames.length > 0 && (
            <span className="muted">
              {pendingPhotoNames.join(', ')} will not be uploaded -- receive this item
              on its own to attach {pendingPhotoNames.length > 1 ? 'them' : 'it'}.
            </span>
          )}
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
        {reviewIds === null && (
          <button type="button" onClick={() => setReviewIds(itemIds)}>
            Confirm or correct fields
          </button>
        )}
        {reviewIds !== null && (
          <ReviewPane ids={reviewIds} onClose={() => setReviewIds(null)} />
        )}
      </div>
    </div>
  )
}
