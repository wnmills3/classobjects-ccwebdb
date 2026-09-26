import { useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import { PRINTING_FACILITIES } from '../../../shared/kinds'
import { webSearchText } from './webSearchText'
import { useDebounced } from '../../../shared/useDebounced'
import { useRequest } from '../../../shared/useRequest'
import HelpScope from '../../HelpScope'

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
  if (row.web_press === true) parts.push('web press')
  if (row.web_press === false) parts.push('sheet-fed')
  return parts.join(' · ')
}

//: The web-press pulldown's values, as the search and record calls take them.
//: Blank is "not known" and filters nothing -- a note without the Web Press
//: attribute is not thereby known to be sheet-fed.
const PRESS = { yes: true, no: false }

/**
 * Open Google AI Mode on `text` in the named pop-up window beside the form.
 *
 * A pop-up, not a tab, so the answer sits beside the form; the name makes a
 * second search reuse that window. Returns what `window.open` does: null
 * when the browser blocked it, which the caller reports.
 */
function openWebSearch(text) {
  return window.open(
    `${AI_SEARCH}${encodeURIComponent(text)}`,
    'friedberg-web-search',
    'popup,width=1000,height=800',
  )
}

//: Google's AI Mode shortcut. It keeps `q` through its redirect (measured
//: 2026-09-23: /ai?q= -> /aimode?q=), where `/search?udm=50` was stripped
//: for a request without a browser session.
const AI_SEARCH = 'https://www.google.com/ai?q='

/** The search and record calls' filters, from the form's fields; blanks omitted. */
function filtersOf(fields) {
  const filters = {}
  if (fields.denomination) filters.denomination = fields.denomination
  if (fields.noteType) filters.note_type = fields.noteType
  if (fields.sealColor) filters.seal_color = fields.sealColor
  if (fields.seriesYear) filters.series_year = Number(fields.seriesYear)
  if (fields.seriesLetter) filters.series_letter = fields.seriesLetter
  if (fields.signatureCombination) {
    filters.signature_combination = fields.signatureCombination
  }
  if (fields.district) filters.district_letter = fields.district
  if (fields.press in PRESS) filters.web_press = PRESS[fields.press]
  // Washington or Fort Worth: a 2017-A $1 is 3005-A from one, 3006-A from
  // the other. The plates are not catalog fields -- they go only into
  // the web search's question, which is how a mule is found.
  if (fields.printing) filters.printing_facility = fields.printing
  return filters
}

/** How long typing must pause before the signature pairs are narrowed again. */
const NARROW_DELAY_MS = 250

const upper = (text) => text.toUpperCase()

/** A vocabulary's values as a code -> label map; empty while it loads. */
function labelsOf(values) {
  return Object.fromEntries((values ?? []).map((entry) => [entry.code, entry.label]))
}

/**
 * The search fields as the note already records them, so the owner starts
 * from what the item says rather than retyping it. Only Web Press can be
 * read from an attribute, and only as "yes": its absence proves nothing.
 */
function fromItem(item) {
  return {
    denomination: item?.denomination ?? '',
    noteType: item?.note_type ?? '',
    sealColor: item?.seal_color ?? '',
    seriesYear: item?.series_year != null ? String(item.series_year) : '',
    seriesLetter: item?.series_letter ?? '',
    signatureCombination: item?.signature_combination ?? '',
    district: item?.fed_district ?? '',
    press: (item?.attributes ?? []).some((a) => a.code === 'web_press') ? 'yes' : '',
    printing: item?.printing_facility ?? '',
    facePlate: item?.face_plate_number ?? '',
    backPlate: item?.back_plate_number ?? '',
  }
}

/**
 * Identify a banknote's Friedberg number from what the owner can see on it.
 *
 * Searches the owner's own accumulating catalog (`GET /friedberg`) -- never
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
 *
 * `item` is the note's detail (`GET /inventory/{id}`), when the caller has
 * it: the fields start from what the note records. `onAttached` is told
 * after a number is attached, so a caller showing the current number can
 * read it again.
 *
 * `searchNow` (the item editor) searches at once with what the note records
 * and hides the fields: the owner asked for the answer, not a form to press
 * Look up on a second time. "Change search fields" shows them for a note
 * whose record is incomplete or wrong. Without it (Receiving) the fields
 * are shown, filled from the note, for the owner to complete first.
 */
export default function FriedbergLookup({
  itemId,
  item,
  onClose,
  onAttached,
  searchNow = false,
}) {
  const [initial] = useState(() => fromItem(item))
  const [showFields, setShowFields] = useState(!searchNow)
  // The search fields, shaped as `fromItem` makes them.
  const [fields, setFields] = useState(initial)
  const {
    denomination,
    noteType,
    sealColor,
    seriesYear,
    seriesLetter,
    signatureCombination,
    district,
    press,
    printing,
    facePlate,
    backPlate,
  } = fields
  /** A change handler for one field, the typed value passed through `clean`. */
  const field =
    (key, clean = (text) => text) =>
    (e) =>
      setFields((current) => ({ ...current, [key]: clean(e.target.value) }))

  const [results, setResults] = useState(null)
  const [searchError, setSearchError] = useState('')
  // Starts true under `searchNow`, rather than being set inside the effect
  // below: the search is already under way on the first render.
  const [searching, setSearching] = useState(searchNow)
  const searchCancelRef = useRef(null)
  const searchTextRef = useRef('')
  const [popupBlocked, setPopupBlocked] = useState(false)

  // The item editor's search, run once with what the note records. It takes
  // the same stale-response guard as a pressed Look up, so a Look up pressed
  // after "Change search fields" supersedes it rather than racing it.
  useEffect(() => {
    if (!searchNow) return undefined
    let cancelled = false
    searchCancelRef.current = () => {
      cancelled = true
    }
    api
      .searchFriedberg(filtersOf(initial))
      .then((rows) => {
        if (cancelled) return
        // Inlined, not `showResults`: the effect runs once, and a function
        // declared in the body would have to be one of its dependencies.
        setResults(rows)
        if (rows.length === 0) setPopupBlocked(!openWebSearch(searchTextRef.current))
      })
      .catch((err) => {
        if (!cancelled) setSearchError(err.message)
      })
      .finally(() => {
        if (!cancelled) setSearching(false)
      })
    return () => {
      cancelled = true
    }
  }, [searchNow, initial])

  const [recordFrNumber, setRecordFrNumber] = useState('')
  const [recordError, setRecordError] = useState('')
  const [recording, setRecording] = useState(false)

  const [attachingKey, setAttachingKey] = useState(null)
  const [attachError, setAttachError] = useState('')
  const [attachMessage, setAttachMessage] = useState('')

  // Every pair, once, so a choice the narrowed list leaves out can still be
  // shown by its name rather than its code -- and the pulldown's list while
  // no series year is entered.
  const everyPair = useRequest('all', () => api.getSignatureChoices({}))
  const allSignatures = everyPair.data?.values ?? []

  // Narrows to the pairs a note of this series can carry -- the seeded
  // `note_issue` facts, the public-fact half of this feature. Not the pairs
  // in office in the series year: that hid every lettered series' later
  // signers (1963-A is Granahan / Fowler). Asked once typing pauses, not per
  // keystroke of the year.
  //
  // A choice the narrowed list leaves out is **kept**, never cleared: it is
  // usually what the note itself records, and clearing it silently is how a
  // right answer vanished before. The pulldown shows it marked instead.
  //
  // A year that is not a whole number ("19x") names no series: nothing is
  // asked, since the server would refuse it, and no pair is offered. It is
  // not read as "no year entered", which would offer every pair.
  const yearNumber = Number(seriesYear)
  const yearUnreadable = seriesYear !== '' && !Number.isInteger(yearNumber)
  const narrowKey = useDebounced(
    seriesYear && !yearUnreadable
      ? JSON.stringify({
          denomination,
          note_type: noteType,
          seal_color: sealColor,
          series_year: yearNumber,
          series_letter: seriesLetter,
        })
      : null,
    NARROW_DELAY_MS,
  )
  const narrowed = useRequest(narrowKey, () =>
    api.getSignatureChoices(JSON.parse(narrowKey)),
  )
  const signatureOptions = yearUnreadable
    ? []
    : narrowKey === null
      ? allSignatures
      : narrowed.error
        ? []
        : (narrowed.data?.values ?? [])

  const denominations = useReference('denomination')
  const noteTypes = useReference('note_type')
  const districts = useReference('fed_district')
  const searchText = webSearchText(fields, {
    denomination: labelsOf(denominations),
    note_type: labelsOf(noteTypes),
    fed_district: labelsOf(districts),
    signature_combination: labelsOf([...allSignatures, ...signatureOptions]),
  })
  // The latest search text, for a search whose answer lands after the
  // vocabularies finish loading -- its question then uses their labels, not
  // the codes the first render had.
  useEffect(() => {
    searchTextRef.current = searchText
  })

  function searchWeb() {
    setPopupBlocked(!openWebSearch(searchText))
  }

  /**
   * What a finished search does: a match shows its number to copy; no
   * match opens the web search straight away, so pressing Look up always
   * ends in an answer or the place to find one.
   */
  function showResults(rows) {
    setResults(rows)
    if (rows.length === 0) setPopupBlocked(!openWebSearch(searchTextRef.current))
  }

  const signatureListed = signatureOptions.some(
    (entry) => entry.code === signatureCombination,
  )
  const signatureLabel =
    allSignatures.find((entry) => entry.code === signatureCombination)?.label ??
    signatureCombination

  function invalidatePendingSearch() {
    searchCancelRef.current?.()
    searchCancelRef.current = null
  }

  const currentFilters = () => filtersOf(fields)

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
      showResults(rows)
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
      onAttached?.(body)
      return body
    } catch (err) {
      setAttachError(err.message)
      return null
    } finally {
      setAttachingKey(null)
    }
  }

  /**
   * Save the number in the field onto this note.
   *
   * A number copied from a match is that catalog row, so it is attached
   * as it is; recording it again would be a 409. Anything else -- typed or
   * pasted from a web search -- is recorded with what the form describes,
   * then attached.
   */
  async function save(status) {
    const number = recordFrNumber.trim()
    const known = (results ?? []).find((row) => row.fr_number === number)
    if (known) {
      setRecordError('')
      if (await attach(known.id, status)) setRecordFrNumber('')
      return
    }
    await recordAndAttach(number, status)
  }

  async function recordAndAttach(number, status) {
    setRecording(true)
    setRecordError('')
    try {
      const created = await api.createFriedbergNumber({
        fr_number: number,
        ...currentFilters(),
      })
      // Recorded now, whatever happens to the attach: listed as a match, so
      // a retry after a failed attach only attaches, instead of recording it
      // again and being refused as a duplicate (code review, 2026-09-23).
      setResults((rows) => [...(rows ?? []), created])
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
      <HelpScope>
        {!showFields && (
          <div className="row">
            <span className="muted">
              {searching
                ? 'Looking up this note...'
                : 'Looked up from what this note records.'}
            </span>
            <button type="button" className="link" onClick={() => setShowFields(true)}>
              Change search fields
            </button>
            {onClose && (
              <button type="button" className="link" onClick={onClose}>
                Close
              </button>
            )}
          </div>
        )}
        {showFields && (
          <>
            <div className="filter-grid">
              <label data-help="denomination">
                Denomination
                <ReferenceSelect
                  table="denomination"
                  value={denomination}
                  onChange={field('denomination')}
                  placeholder="usd_note_5_00"
                />
              </label>
              <label data-help="note_type">
                Note type
                <ReferenceSelect
                  table="note_type"
                  value={noteType}
                  onChange={field('noteType')}
                  placeholder="federal_reserve_note"
                />
              </label>
              <label data-help="seal_color">
                Seal color
                <ReferenceSelect
                  table="seal_color"
                  value={sealColor}
                  onChange={field('sealColor')}
                  placeholder="green"
                />
              </label>
              <label data-help="series_year">
                Series year
                <input
                  type="text"
                  inputMode="numeric"
                  value={seriesYear}
                  onChange={field('seriesYear')}
                />
              </label>
              <label data-help="series_letter">
                Series letter
                <input
                  type="text"
                  maxLength={1}
                  value={seriesLetter}
                  onChange={field('seriesLetter', upper)}
                />
              </label>
              <label data-help="signature_combination">
                Signature combination
                <select
                  value={signatureCombination}
                  onChange={field('signatureCombination')}
                >
                  <option value="">--</option>
                  {signatureCombination && !signatureListed && (
                    <option value={signatureCombination}>
                      {signatureLabel} (not listed for this series)
                    </option>
                  )}
                  {signatureOptions.map((entry) => (
                    <option key={entry.code} value={entry.code}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </label>
              <label data-help="fed_district">
                District
                <ReferenceSelect
                  table="fed_district"
                  value={district}
                  onChange={field('district')}
                  placeholder="B"
                />
              </label>
              <label data-help="web_press">
                Web press
                <select value={press} onChange={field('press')}>
                  <option value="">Not known</option>
                  <option value="yes">Yes</option>
                  <option value="no">No, sheet-fed</option>
                </select>
              </label>
              <label data-help="printing_facility">
                Printed at
                <select value={printing} onChange={field('printing')}>
                  <option value="">Not known</option>
                  {PRINTING_FACILITIES.map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label data-help="face_plate_number">
                Face plate
                <input
                  type="text"
                  value={facePlate}
                  onChange={field('facePlate', upper)}
                />
              </label>
              <label data-help="back_plate_number">
                Back plate
                <input
                  type="text"
                  inputMode="numeric"
                  value={backPlate}
                  onChange={field('backPlate')}
                />
              </label>
            </div>

            <div className="row">
              {/* Not disabled while a search is already running: refining a filter
            and pressing this again before a slow response lands is a normal
            way to use the form, not a mistake to block. The stale-response
            guard above (`searchCancelRef`) is what keeps that safe, the same
            idiom `ItemFinder` uses for its own "Find" button. */}
              <button type="button" onClick={search}>
                {searching ? 'Looking up...' : 'Look up'}
              </button>
              {onClose && (
                <button type="button" className="link" onClick={onClose}>
                  Close
                </button>
              )}
            </div>
          </>
        )}

        {searchError && <p className="error">{searchError}</p>}
        {popupBlocked && (
          <p className="error">
            The browser blocked the search window -- press Search the web to open it.
          </p>
        )}

        {/* One field, one pair of Save buttons. Found in the catalog: each
          match has a Copy button that puts its number in the field. Not
          found: the field stays blank and the web search opens -- the owner
          reads the number off the results window and types or pastes it
          here. Search the web is always offered, for a match that is wrong.
          Nothing is fetched or saved from the search itself (see
          `webSearchText`). */}
        {results && (
          <div className="admin-form">
            {results.length > 0 ? (
              <ul className="order-picker">
                {results.map((row) => (
                  // The number and its Copy button, nothing else: what is
                  // copied is exactly what is shown. The row's description and
                  // whether it is verified are on hover, for telling two
                  // matches apart.
                  <li
                    key={row.id}
                    className="order-row row"
                    title={[
                      describeMatch(row),
                      row.verified ? 'verified' : 'unverified proposal',
                    ]
                      .filter(Boolean)
                      .join(' -- ')}
                  >
                    <span className="mono">{row.fr_number}</span>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Copy ${row.fr_number}`}
                      onClick={() => setRecordFrNumber(row.fr_number)}
                    >
                      Copy
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">
                No match in the catalog yet, so the web search has opened in its own
                window -- type or paste the number here. An AI answer can be wrong: save
                it as proposed until you have checked it against the note or a
                reference.
              </p>
            )}
            <div className="row">
              <label data-help="fr_number">
                Fr. number
                <input
                  type="text"
                  value={recordFrNumber}
                  onChange={(e) => setRecordFrNumber(e.target.value)}
                />
              </label>
              {/* Offered with matches too: a match can be a wrong number
                  recorded earlier, and the web is where the right one is
                  found (owner, 2026-09-24). Only a miss opens it unasked. */}
              <button type="button" onClick={searchWeb} title={searchText}>
                Search the web
              </button>
            </div>
            {recordError && <p className="error">{recordError}</p>}
            <div className="row">
              <button
                type="button"
                disabled={!recordFrNumber.trim() || busy}
                onClick={() => save('proposed')}
              >
                Save as proposed
              </button>
              <button
                type="button"
                disabled={!recordFrNumber.trim() || busy}
                onClick={() => save('confirmed')}
              >
                Save as confirmed
              </button>
            </div>
          </div>
        )}

        {attachError && <p className="error">{attachError}</p>}
        {attachMessage && <p className="muted">{attachMessage}</p>}
      </HelpScope>
    </div>
  )
}
