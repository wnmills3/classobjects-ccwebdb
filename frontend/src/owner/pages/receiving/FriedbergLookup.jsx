import { useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'

//: A matched row's classifiers, rendered as one readable line -- only the
//: attributes the row actually knows are shown, since a half-known row (see
//: the backend's own docstring on `search_friedberg`) may not have all of
//: them.
function describeMatch(row) {
  const parts = []
  if (row.denomination) parts.push(row.denomination)
  if (row.note_type) parts.push(row.note_type)
  if (row.series_year) parts.push(`${row.series_year}${row.series_letter ?? ''}`)
  if (row.seal_color) parts.push(row.seal_color)
  if (row.signature_combination) parts.push(row.signature_combination)
  if (row.district_letter) parts.push(`district ${row.district_letter}`)
  return parts.join(' · ')
}

/**
 * Identify a banknote's Friedberg number from what the owner can see on it.
 *
 * Searches the owner's own accumulating catalogue (`GET /friedberg`) -- never
 * a licensed dataset. The table ships empty by design (see `CLAUDE.md`'s ban
 * on shipping a publisher's arrangement), so most of what this renders, for a
 * long while, is the "no match" path: record the number read off the note or
 * slab (`POST /friedberg`) and attach it (`POST /inventory/{id}/friedberg`).
 *
 * The owner asks for this explicitly -- nothing here runs until "Look up" is
 * pressed, unlike the signature-combination narrowing, which is public fact
 * (already seeded server-side) and safe to refresh as soon as a year is
 * typed.
 *
 * A search is guarded against a stale response the same way `ItemFinder`
 * guards its own "Find" click: a cancel function stashed in a ref, replaced
 * before every new search, so a slow response for an abandoned query can
 * never land after a faster, later one and make a result clickable that no
 * longer matches what is on screen.
 */
export default function FriedbergLookup({ itemId, onClose }) {
  const [denomination, setDenomination] = useState('')
  const [noteType, setNoteType] = useState('')
  const [sealColor, setSealColor] = useState('')
  const [seriesYear, setSeriesYear] = useState('')
  const [seriesLetter, setSeriesLetter] = useState('')
  const [signatureCombination, setSignatureCombination] = useState('')
  const [signatureOptions, setSignatureOptions] = useState([])

  const [results, setResults] = useState(null)
  const [searchError, setSearchError] = useState('')
  const [searching, setSearching] = useState(false)
  const searchCancelRef = useRef(null)

  const [recordFrNumber, setRecordFrNumber] = useState('')
  const [recordError, setRecordError] = useState('')
  const [recording, setRecording] = useState(false)

  const [attachingKey, setAttachingKey] = useState(null)
  const [attachError, setAttachError] = useState('')
  const [attachMessage, setAttachMessage] = useState('')

  // Narrows to the pairs whose term covers `seriesYear` -- the public-fact
  // half of this feature, already seeded server-side. With no year entered,
  // `getSignatureCombinations` is called with `undefined`, which the backend
  // treats as "no year filter" and returns the unnarrowed list, rather than
  // this offering an empty pulldown before the owner has typed anything.
  useEffect(() => {
    let cancelled = false
    const year = seriesYear ? Number(seriesYear) : undefined
    api
      .getSignatureCombinations(year)
      .then((body) => {
        if (cancelled) return
        setSignatureOptions(body.values)
        // A choice the previous, wider list allowed can fall outside a newly
        // narrowed one -- clear it rather than submit a code the pulldown no
        // longer offers.
        setSignatureCombination((prev) =>
          prev && !body.values.some((entry) => entry.code === prev) ? '' : prev,
        )
      })
      .catch(() => {
        if (!cancelled) setSignatureOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [seriesYear])

  function invalidatePendingSearch() {
    searchCancelRef.current?.()
    searchCancelRef.current = null
  }

  function currentFilters() {
    const filters = {}
    if (denomination) filters.denomination = denomination
    if (noteType) filters.note_type = noteType
    if (sealColor) filters.seal_color = sealColor
    if (seriesYear) filters.series_year = Number(seriesYear)
    if (seriesLetter) filters.series_letter = seriesLetter
    if (signatureCombination) filters.signature_combination = signatureCombination
    return filters
  }

  async function search() {
    invalidatePendingSearch()
    let cancelled = false
    searchCancelRef.current = () => {
      cancelled = true
    }

    setSearching(true)
    setSearchError('')
    setAttachError('')
    setAttachMessage('')
    try {
      const rows = await api.searchFriedberg(currentFilters())
      if (cancelled) return
      setResults(rows)
    } catch (err) {
      if (cancelled) return
      setSearchError(err.message)
      setResults(null)
    } finally {
      if (!cancelled) setSearching(false)
    }
  }

  // Shared by "attach a result" and "record, then attach" -- returns the
  // attach response on success or null on failure, so a caller that needs to
  // know which happened (recording clears its input only on success) can
  // tell without the failure being swallowed.
  async function attach(friedbergId, status) {
    setAttachingKey(`${friedbergId}:${status}`)
    setAttachError('')
    setAttachMessage('')
    try {
      const body = await api.attachFriedberg(itemId, {
        friedberg_id: friedbergId,
        status,
      })
      setAttachMessage(
        `Attached ${body.fr_number} to this item as ${body.friedberg_status}.`,
      )
      return body
    } catch (err) {
      setAttachError(err.message)
      return null
    } finally {
      setAttachingKey(null)
    }
  }

  async function recordAndAttach(status) {
    setRecording(true)
    setRecordError('')
    try {
      const created = await api.createFriedbergNumber({
        fr_number: recordFrNumber,
        ...currentFilters(),
      })
      const attached = await attach(created.id, status)
      if (attached) {
        setRecordFrNumber('')
        setResults(null)
      }
    } catch (err) {
      // A duplicate fr_number is a 409 naming the row already on file -- the
      // owner will legitimately hit this when the same type arrives twice,
      // so the message is shown, not swallowed.
      setRecordError(err.message)
    } finally {
      setRecording(false)
    }
  }

  const busy = attachingKey != null || recording

  return (
    <div className="friedberg-lookup">
      <div className="filter-grid">
        <label>
          Denomination
          <ReferenceSelect
            table="denomination"
            value={denomination}
            onChange={(e) => setDenomination(e.target.value)}
            placeholder="usd_note_5_00"
          />
        </label>
        <label>
          Note type
          <ReferenceSelect
            table="note_type"
            value={noteType}
            onChange={(e) => setNoteType(e.target.value)}
            placeholder="federal_reserve_note"
          />
        </label>
        <label>
          Seal color
          <ReferenceSelect
            table="seal_color"
            value={sealColor}
            onChange={(e) => setSealColor(e.target.value)}
            placeholder="green"
          />
        </label>
        <label>
          Series year
          <input
            type="text"
            inputMode="numeric"
            value={seriesYear}
            onChange={(e) => setSeriesYear(e.target.value)}
          />
        </label>
        <label>
          Series letter
          <input
            type="text"
            maxLength={1}
            value={seriesLetter}
            onChange={(e) => setSeriesLetter(e.target.value.toUpperCase())}
          />
        </label>
        <label>
          Signature combination
          <select
            value={signatureCombination}
            onChange={(e) => setSignatureCombination(e.target.value)}
          >
            <option value="">--</option>
            {signatureOptions.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="row">
        <button type="button" disabled={searching} onClick={search}>
          Look up
        </button>
        {onClose && (
          <button type="button" className="link" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      {searchError && <p className="error">{searchError}</p>}

      {results && results.length > 0 && (
        <ul className="order-picker">
          {results.map((row) => (
            <li key={row.id} className="order-row">
              <span className="mono">{row.fr_number}</span> {describeMatch(row)}
              {' -- '}
              {row.verified ? (
                <strong>Verified</strong>
              ) : (
                <span className="muted">Unverified proposal</span>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() => attach(row.id, 'proposed')}
              >
                Attach as proposed
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => attach(row.id, 'confirmed')}
              >
                Attach as confirmed
              </button>
            </li>
          ))}
        </ul>
      )}

      {results && results.length === 0 && (
        <div className="admin-form">
          <p className="muted">
            No match in the catalogue yet -- record the number read off the note or
            slab.
          </p>
          <label>
            Fr. number
            <input
              type="text"
              value={recordFrNumber}
              onChange={(e) => setRecordFrNumber(e.target.value)}
            />
          </label>
          {recordError && <p className="error">{recordError}</p>}
          <div className="row">
            <button
              type="button"
              disabled={!recordFrNumber || busy}
              onClick={() => recordAndAttach('proposed')}
            >
              Record &amp; attach as proposed
            </button>
            <button
              type="button"
              disabled={!recordFrNumber || busy}
              onClick={() => recordAndAttach('confirmed')}
            >
              Record &amp; attach as confirmed
            </button>
          </div>
        </div>
      )}

      {attachError && <p className="error">{attachError}</p>}
      {attachMessage && <p className="muted">{attachMessage}</p>}
    </div>
  )
}
