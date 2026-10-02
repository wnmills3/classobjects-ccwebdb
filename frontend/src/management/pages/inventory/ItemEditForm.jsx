import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { money } from '../../../shared/format'
import { api } from '../../api'
import {
  fieldFitsKind,
  fitsKind,
  gradeFitsKind,
  isCurrencyKind,
} from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import { seriesYearProblem } from '../../series-years'
import { AccessLabel } from '../../AccessLabel'
import { accel, useSaveShortcut } from '../../shortcuts'
import ForSaleNotice from '../ForSaleNotice'
import ErrorsPanel from './ErrorsPanel'
import LocationSelect from '../../LocationSelect'
import AttributesField from './AttributesField'
import ConflictList from './ConflictList'
import { baseFor, conflictsOf, fieldName, rebase, shown } from './fieldMerge'
import { clearedByKind } from './kindChange'
import FriedbergPanel from './FriedbergPanel'
import HelpScope from '../../HelpScope'
import HistoryPanel from './HistoryPanel'
import NoteFields from './NoteFields'
import OffersPanel from './OffersPanel'
import PhotosPanel from './PhotosPanel'
import SaleHistory from './SaleHistory'
import SplitDialog from './SplitDialog'
import { SaveButton } from '../../SaveButton'

/**
 * One item, every field, with what the lot claimed beside each.
 *
 * Two provenance marks appear on every field and they mean different things.
 * "lot says BU" is *derived* by comparing this piece to its parent: it says
 * what the seller claimed about the whole lot, and it recomputes so it cannot
 * drift. The confirm box is *asserted*: it says a person looked at this coin.
 * Neither is computable from the other, which is why both are here.
 */

//: How often an open form checks for changes made elsewhere. It also checks
//: whenever the window gets focus back, which is when it matters most.
const CHECK_EVERY_MS = 15000

const TEXT_FIELDS = [
  ['Title', 'source_title', 't'],
  ['Description', 'description', 'c'],
]

const NUMBER_FIELDS = [['Pieces', 'piece_count', 'p']]

/**
 * Whether stored years are a range rather than one year.
 *
 * One year is stored as start == end, and a few older items as a start with
 * no end; both are one year. A range is for a multi-year set, or a coin dated
 * only to an era -- rare -- so the form asks for one year unless the item
 * already has a range. Opening a stored range as one would let a
 * save collapse it without anyone seeing the end year.
 */
function isRange(start, end) {
  return start != null && end != null && Number(start) !== Number(end)
}

/** An emptied number box clears the year rather than sending "". */
const yearValue = (text) => (text === '' ? null : text)

const MONEY_FIELDS = [
  ['Item cost', 'item_cost', 'i'],
  ['Shipping', 'shipping_cost', 'h'],
]

//: Form field -> the column a review record names. Only these can be
//: confirmed; the rest have no review box.
const REVIEWABLE = {
  year_start: 'year_start',
  year_end: 'year_end',
  piece_count: 'piece_count',
  grade: 'grade_id',
  strike_type: 'strike_type_id',
  denomination: 'denomination_id',
  country: 'country_id',
  metal: 'metal_id',
}

const CLASSIFIERS = [
  // A coin's grade is a number; its strike type says whether 65 is MS65 or
  // PR65. A note has no strike type, so the box is not shown for one.
  ['Strike type', 'strike_type', 'strike_type', 'k'],
  ['Grade', 'grade', 'grade', 'g'],
  // One per grade and part of it -- PMG's "64 EPQ", a coin's "65 DCAM" --
  // offered only for the item's kind. No letters left for these two.
  ['Grade designation', 'grade_designation', 'grade_designation', null],
  ['Grading service', 'grading_service', 'grading_service', null],
  // A coin's own detail. `P` is Philadelphia; blank is not recorded.
  ['Mint', 'mint', 'mint', null],
  ['Denomination', 'denomination', 'denomination', 'm'],
  // What kind of set -- proof set, mint set, mixed sets. A coin's side only.
  ['Set form', 'set_form', 'set_form', null],
  ['Country', 'country', 'country', 'u'],
  ['Metal', 'metal', 'metal', 'l'],
  // Status is editable here because Receiving only moves an item forward:
  // this is the way back for a parcel recorded as received in error, or
  // against the wrong row. `PATCH /api/inventory/{id}` routes it through
  // `set_status`, so the status-history row is written -- an item's history
  // stays a true account of where it has been, including the correction.
  ['Status', 'status', 'item_status', 's'],
]

//: Where a derived value came from, as the "suggested" mark's tooltip says it.
const DERIVED_FROM = {
  note_issue: "Filled from the note's denomination and series",
  serial_district: 'Filled from the serial number',
  composition: 'Filled from the published composition for its year',
  series_classify: 'Filled from the denomination and year',
  series_match: 'Filled from the description',
  series_backfill: 'Filled from the description or from denomination and year',
  suggestion: 'Suggested when the item was entered',
  rating: 'Read from the rating',
}

/** The column a form field is stored in, as `derived` and reviews name it. */
const columnOf = (key, isClassifier) => (isClassifier ? `${key}_id` : key)

//: Vocabularies this form must not let anyone extend. `item_status` is a
//: lifecycle the code branches on, not a descriptive list that grows with
//: use -- see `ReferenceSelect`'s `allowAdd`.
//: Designations and grading services are the graders' own lists, seeded
//: with the coin/note side each belongs to; a typed-in one would have none.
const FIXED_VOCABULARIES = new Set([
  'item_status',
  'strike_type',
  'grade_designation',
  'grading_service',
  'mint',
  // A denomination is a face value with a currency and a side, which an
  // add-by-label form cannot give it: the server would refuse it.
  'denomination',
])

/**
 * Certificate numbers as typed: comma-separated. Split as the user types,
 * keeping a trailing empty entry so the comma just typed stays on screen;
 * empties are dropped when the save is sent.
 */
const certsFrom = (text) => text.split(',').map((part) => part.trim())

/** What Save says when held photographs could not be filed. */
function photosFailed(failed) {
  const what =
    failed.length === 1 ? 'A photograph was' : `${failed.length} photographs were`
  return `${what.toLowerCase()} not added: ${failed.map((entry) => entry.error).join('; ')}. Fix or discard ${failed.length === 1 ? 'it' : 'them'} below, then Save again`
}

/**
 * An error set compared as a set: the same types with the same notes, in any
 * order, is no change to save.
 */
function errorsKey(rows) {
  return JSON.stringify(
    rows
      .map((row) => [row.error_type, row.details ?? null])
      .sort((a, b) => a[0].localeCompare(b[0])),
  )
}

export default function ItemEditForm({ itemId, onSaved, onChanged, onClose }) {
  const [item, setItem] = useState(null)
  const [draft, setDraft] = useState({})
  // Where each edited field's edit began (see `fieldMerge.js`): a change made
  // elsewhere matters only to a field being edited here, and only this says
  // whether it happened.
  const [baseItem, setBaseItem] = useState(null)
  // When the form last took in changes made elsewhere, to say so.
  const [refreshedAt, setRefreshedAt] = useState(null)
  const draftRef = useRef(draft)
  const versionRef = useRef(null)
  const [reviewed, setReviewed] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  // Photographs added in the Photos panel, held until Save files them with
  // the rest of the edit. Each keeps the error its filing met, if any.
  const [pendingPhotos, setPendingPhotos] = useState([])
  const [photoKey, setPhotoKey] = useState(0)
  // Changes to photographs already filed -- a role, the primary, a removal
  // -- keyed by link id, held the same way (see PhotosPanel).
  const [photoEdits, setPhotoEdits] = useState({})
  // A Friedberg number confirmed, chosen or cleared in its panel, held for
  // Save the same way.
  const [pendingFriedberg, setPendingFriedberg] = useState(null)
  // The item's errors as last read or recorded, and as edited here -- held
  // for Save the same way, and recorded as a whole set. Both null until
  // read; a read that failed leaves its reason and nothing to edit, since a
  // set edited from nothing would replace, on Save, the one never seen.
  const [savedErrors, setSavedErrors] = useState(null)
  const [errors, setErrors] = useState(null)
  const [errorsReadError, setErrorsReadError] = useState('')
  const [ranged, setRanged] = useState(false)
  // Ticked to change an item that is for sale; reset whenever it is loaded.
  const [acknowledged, setAcknowledged] = useState(false)
  // What the last change of kind emptied, so it can be named on screen and
  // put back if the kind is changed back: key -> {had, prev, was}.
  const [kindCleared, setKindCleared] = useState({})
  // What the Suggest button last said: a note under the description.
  const [suggestNote, setSuggestNote] = useState('')
  // The Split dialog, while open; and what the last split made, to say so.
  const [splitting, setSplitting] = useState(false)
  const [splitNote, setSplitNote] = useState('')
  const kinds = useReference('item_kind')
  const vocab = {
    denomination: useReference('denomination'),
    series: useReference('series'),
    grade: useReference('grade'),
    grade_designation: useReference('grade_designation'),
    item_attribute: useReference('item_attribute'),
  }
  const yearId = useId()
  const yearEndId = useId()

  const forSale = (item?.sale_state ?? []).length > 0
  // What the server ends an offer for: a status or disposition that differs
  // from the one the item holds.
  const endsOffer = ['status', 'disposition'].some(
    (key) => key in draft && draft[key] !== item?.[key],
  )
  // Fields edited here that someone else has changed since: each waits for a
  // choice before the form can be saved.
  const conflicts = item && baseItem ? conflictsOf(item, baseItem, draft) : []
  const hasFields = Object.keys(draft).length > 0
  const errorsChanged = errors !== null && errorsKey(errors) !== errorsKey(savedErrors)
  const canSave =
    !saving &&
    (hasFields ||
      pendingPhotos.length > 0 ||
      Object.keys(photoEdits).length > 0 ||
      pendingFriedberg !== null ||
      errorsChanged) &&
    (!forSale || acknowledged) &&
    // A photograph filed without saying what it shows is one nobody finds.
    pendingPhotos.every((entry) => entry.role) &&
    conflicts.length === 0
  // `save` is a function declaration below, hoisted for the whole component
  // scope, so it is safe to reference here even though it is defined later --
  // this hook must sit above every early return.
  useSaveShortcut(save, canSave)

  /**
   * Take `body` as the item now open -- a first load, or a save read back --
   * with nothing edited: the draft, the kind change and the for-sale tick
   * all start again from it.
   */
  const adopt = useCallback((body) => {
    setItem(body)
    setBaseItem(body)
    setReviewed(body.reviewed ?? [])
    setRanged(isRange(body.year_start, body.year_end))
    setDraft({})
    setKindCleared({})
    setAcknowledged(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .getInventoryItem(itemId)
      .then((body) => {
        if (cancelled) return
        adopt(body)
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [itemId, adopt])

  useEffect(() => {
    let cancelled = false
    api
      .getItemErrors(itemId)
      .then((body) => {
        if (cancelled) return
        const rows = (body.errors ?? []).map((e) => ({
          error_type: e.error_type,
          details: e.details ?? null,
        }))
        setSavedErrors(rows)
        setErrors(rows)
      })
      .catch((err) => {
        if (!cancelled) setErrorsReadError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [itemId])

  // The latest draft and version, for the check below, which runs on a timer.
  useEffect(() => {
    draftRef.current = draft
    versionRef.current = item?.version ?? null
  })

  // Changes made elsewhere -- another person, another tab -- come in while
  // this form is open: checked every so often and whenever the window gets
  // focus back. A field not being edited here takes the new value at once; a
  // field being edited keeps its base, so a change to it shows as a conflict
  // to resolve rather than being overwritten or adopted unseen.
  useEffect(() => {
    let cancelled = false
    function check() {
      api
        .getInventoryItem(itemId)
        .then((fresh) => {
          if (cancelled || versionRef.current == null) return
          if (fresh.version === versionRef.current) return
          setItem(fresh)
          setReviewed(fresh.reviewed ?? [])
          setBaseItem((previous) => rebase(fresh, previous, draftRef.current))
          setRefreshedAt(new Date())
        })
        .catch(() => {
          // A missed check is caught by the next one, or by the save.
        })
    }
    const timer = setInterval(check, CHECK_EVERY_MS)
    window.addEventListener('focus', check)
    return () => {
      cancelled = true
      clearInterval(timer)
      window.removeEventListener('focus', check)
    }
  }, [itemId])

  /**
   * Read the item again after something else changed it on the server.
   *
   * Offering or ending an offer from the panel below changes `sale_state`,
   * and `sale_state` is what decides whether a save has to be acknowledged.
   * Without this, offering an item and then saving an edit is refused by the
   * server for a reason nothing on screen can explain: the form still holds
   * the sale state the item had before it was offered.
   *
   * Deliberately NOT the load effect: that one clears the draft, and an edit
   * typed before the offer was made is the operator's work, not something to
   * throw away. Only what the server owns is replaced.
   */
  function reloadItem() {
    api
      .getInventoryItem(itemId)
      .then((body) => {
        setItem(body)
        setReviewed(body.reviewed ?? [])
        // Edited fields keep their base: a change made elsewhere to one of
        // them shows as a conflict, never silently adopted.
        setBaseItem((previous) => rebase(body, previous, draftRef.current))
      })
      .catch((err) => setError(err.message))
  }

  if (error && !item) return <p className="error">{error}</p>
  if (!item) return <p className="muted">Loading...</p>

  // `in`, not `??`: a year cleared in the draft is null, and must not fall
  // back to showing the stored year it is about to replace.
  const value = (key) => (key in draft ? draft[key] : item[key]) ?? ''
  // A coin dated outside its design series' years is almost always a typo:
  // an 1800 Morgan dollar.
  const yearWarning =
    item && !isCurrencyKind(value('item_kind'))
      ? seriesYearProblem(
          (vocab.series ?? []).find((entry) => entry.code === value('series')),
          value('year_start'),
          value('year_end'),
        )
      : ''
  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

  // A new kind empties what the item can no longer have (`kindChange.js`).
  // The previous change's clearing is undone first, so going Coin -> Currency
  // -> Coin ends where it began, with nothing left to save.
  function changeKind(e) {
    const kind = e.target.value
    const next = { ...draft }
    for (const [key, { had, prev }] of Object.entries(kindCleared)) {
      if (had) next[key] = prev
      else delete next[key]
    }
    if (kind === item.item_kind) delete next.item_kind
    else next.item_kind = kind

    const current = (key) =>
      key in next
        ? next[key]
        : key === 'attributes'
          ? (item.attributes ?? []).map((a) => a.code)
          : item[key]
    const { fields, attributes } = clearedByKind(kind, current, vocab)
    const cleared = {}
    for (const [key, was] of Object.entries(fields)) {
      cleared[key] = { had: key in next, prev: next[key], was }
      next[key] = null
    }
    if (attributes) {
      cleared.attributes = {
        had: 'attributes' in next,
        prev: next.attributes,
        was: attributes.dropped,
      }
      next.attributes = attributes.keep
    }
    setDraft(next)
    setKindCleared(cleared)
  }

  const kindLabel = (code) => kinds?.find((k) => k.code === code)?.label ?? code
  const leavingNote = item.item_kind === 'currency' && value('item_kind') !== 'currency'

  // One year is both ends. Sending both, rather than the start alone, is what
  // turns an item stored with a start and no end into one year as well.
  function setYear(e) {
    const year = yearValue(e.target.value)
    setDraft({ ...draft, year_start: year, year_end: year })
  }

  function toggleRange(e) {
    setRanged(e.target.checked)
    if (e.target.checked) return
    // Back to one year: the end follows the start. On an item that was one
    // year with no start edited, that is no change at all, so nothing is left
    // in the draft to save.
    const next = { ...draft }
    if (!isRange(item.year_start, item.year_end) && !('year_start' in draft)) {
      delete next.year_end
    } else {
      next.year_end = 'year_start' in draft ? draft.year_start : item.year_start
    }
    setDraft(next)
  }

  // A rate of zero means no tax was charged. Unticking goes back to the rate
  // the item was bought at, if it had one -- ticking and unticking is then no
  // change at all. An item recorded as untaxed has no rate of its own to go
  // back to, so it takes the configured one.
  const taxRate = draft.tax_rate ?? item.tax_rate
  const untaxed = taxRate !== undefined && Number(taxRate) === 0
  function toggleTax(e) {
    const next = { ...draft }
    if (e.target.checked) next.tax_rate = '0'
    else if (Number(item.tax_rate) !== 0) delete next.tax_rate
    else next.tax_rate = item.default_tax_rate
    setDraft(next)
  }

  // One column or several: one year is confirmed as both of its ends at once,
  // since a person looking at the coin confirmed the one year it shows.
  function toggleReview(columns) {
    const cols = [].concat(columns)
    const before = reviewed
    const next = cols.every((c) => reviewed.includes(c))
      ? reviewed.filter((c) => !cols.includes(c))
      : [...new Set([...reviewed, ...cols])]
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

  function claimed(text) {
    // An empty cell rather than nothing: `.field` is a four-column grid, and a
    // missing child shifts everything after it into the wrong column.
    if (text === undefined || text === null) return <span />
    return (
      <span
        className="lot-claim"
        title={`The lot ${item.parent_item_code} claimed this`}
      >
        lot says {String(text)}
      </span>
    )
  }

  const claim = (column) => claimed(item.lot_claims?.[column])

  // A value the facts filled in, until someone changes it: saving a field by
  // hand makes it theirs, and the server drops the mark.
  function suggested(key, column) {
    const rule = item.derived?.[column]
    if (!rule || key in draft) return null
    return (
      <span className="suggested" title={DERIVED_FROM[rule] ?? rule}>
        suggested
      </span>
    )
  }

  // The third grid cell: what the lot claimed, and whether the value was
  // filled in from the facts. One element either way, so the grid holds.
  function side(key, column) {
    return (
      <span>
        {claim(key)}
        {suggested(key, column)}
      </span>
    )
  }

  // The lot's years in the same shape as the one Year box: its range if it
  // claimed one, otherwise its year.
  function yearClaim() {
    const start = item.lot_claims?.year_start
    const end = item.lot_claims?.year_end
    return claimed(isRange(start, end) ? `${start}-${end}` : (start ?? end))
  }

  function review(columns) {
    if (!columns) return <span />
    const cols = [].concat(columns)
    return (
      <label
        className="review-mark"
        title="I have confirmed this by examination"
        data-help="reviewed"
      >
        <input
          type="checkbox"
          checked={cols.every((c) => reviewed.includes(c))}
          onChange={() => toggleReview(cols)}
        />
        {/* */}
        confirmed
      </label>
    )
  }

  // No date: the piece carries none at all (a gold bar), as distinct from a
  // year not recorded. Ticking it clears the years in the same save; the
  // server refuses the two together.
  const noDate = value('no_date') === true
  function toggleNoDate(e) {
    if (e.target.checked) {
      setRanged(false)
      setDraft({ ...draft, no_date: true, year_start: null, year_end: null })
    } else {
      setDraft({ ...draft, no_date: false })
    }
  }

  const rangeToggle = (
    <>
      <label className="checkbox" data-help="year_range">
        <input
          type="checkbox"
          checked={ranged}
          onChange={toggleRange}
          disabled={noDate}
          {...accel('r')}
        />
        {/* */}
        <AccessLabel text="Range of years" accessKey="r" />
      </label>
      <label className="checkbox" data-help="no_date">
        <input type="checkbox" checked={noDate} onChange={toggleNoDate} />
        {/* */}
        No date
      </label>
    </>
  )

  // The suggestion reads the screen: every unsaved change, as Save would
  // send it, and the errors the panel holds.
  async function suggestDescription() {
    // Not the description itself: the suggestion is what replaces it.
    const changes = { ...draft }
    delete changes.description
    if (Array.isArray(changes.cert_numbers)) {
      changes.cert_numbers = changes.cert_numbers.filter(Boolean)
    }
    const body = { changes }
    if (errorsChanged) {
      body.errors = errors.map((row) => ({
        error_type: row.error_type,
        details: row.details ?? null,
      }))
    }
    try {
      const { description } = await api.suggestDescriptionFromScreen(itemId, body)
      if (!description) {
        setSuggestNote('Nothing recorded yet to describe it from.')
        return
      }
      // Into the draft only: the owner edits it, and Save keeps it.
      setDraft({ ...draft, description })
      setSuggestNote('Suggested from what is shown -- edit it, then Save to keep it.')
    } catch (err) {
      setSuggestNote(err.message)
    }
  }

  /**
   * File each held photograph; those that fail stay held with their reason.
   * Returns how many failed.
   */
  async function filePhotos() {
    const ack = forSale && acknowledged
    const left = []
    for (const entry of pendingPhotos) {
      try {
        if (entry.kind === 'file') {
          await api.uploadImage(itemId, entry.file, {
            imageRole: entry.role,
            acknowledgeForSale: ack,
          })
        } else {
          await api.addImageFromUrl(itemId, entry.url, {
            imageRole: entry.role,
            acknowledgeForSale: ack,
          })
        }
      } catch (err) {
        left.push({ ...entry, error: err.message })
      }
    }
    setPendingPhotos(left)
    if (pendingPhotos.length > 0) setPhotoKey((n) => n + 1)
    return left
  }

  /**
   * Apply the held changes to filed photographs; those that fail stay held
   * with their reason. Returns the failures' reasons.
   *
   * Roles and a new primary go first, then moves to other items, removals
   * last: a primary moved off a photograph that then leaves has already been
   * given its successor, so the server's own fill-in
   * (`fill_primary_vacancy`) never overrides it.
   */
  async function applyPhotoEdits() {
    const ack = forSale && acknowledged
    const entries = Object.entries(photoEdits)
    const ordered = [
      ...entries.filter(([, edit]) => !edit.remove && !edit.move_to),
      ...entries.filter(([, edit]) => edit.move_to),
      ...entries.filter(([, edit]) => edit.remove),
    ]
    const left = {}
    for (const [id, edit] of ordered) {
      try {
        if (edit.remove) {
          await api.detachImage(Number(id), { acknowledgeForSale: ack })
        } else if (edit.move_to) {
          await api.moveImageLink(Number(id), {
            inventoryItemId: edit.move_to.id,
            acknowledgeForSale: ack,
          })
        } else {
          const change = { acknowledgeForSale: ack }
          if ('image_role' in edit) change.imageRole = edit.image_role
          if (edit.is_primary) change.isPrimary = true
          await api.updateImageLink(Number(id), change)
        }
      } catch (err) {
        left[id] = { ...edit, error: err.message }
      }
    }
    setPhotoEdits(left)
    if (entries.length > 0) setPhotoKey((n) => n + 1)
    return Object.values(left).map((edit) => edit.error)
  }

  /** Apply a held Friedberg change; its failure is kept on it and returned. */
  async function applyFriedberg(held) {
    try {
      if (held.action === 'clear') await api.clearFriedberg(itemId)
      else
        await api.attachFriedberg(itemId, {
          friedberg_id: held.friedberg_id,
          status: held.status,
        })
      setPendingFriedberg(null)
      return null
    } catch (err) {
      setPendingFriedberg({ ...held, error: err.message })
      return err.message
    }
  }

  /**
   * Save everything held: a Friedberg number cleared first (the server
   * refuses a note that stops being one while it has a number), then the
   * fields, then a Friedberg number attached, then the errors, then the
   * changes to filed photographs, then new photographs. What
   * fails stays held with its reason; the editor closes only when all of it
   * is saved.
   */
  async function save() {
    setSaving(true)
    const heldFriedberg = pendingFriedberg
    const problems = []

    if (heldFriedberg?.action === 'clear') {
      const failed = await applyFriedberg(heldFriedberg)
      if (failed) {
        setError(
          `The Friedberg number was not cleared: ${failed}. Nothing else was saved.`,
        )
        setSaving(false)
        return
      }
    }

    if (hasFields) {
      try {
        // `base` makes the save field by field: a change made elsewhere since
        // stops it only where it touched a field changed here (409 naming
        // them). `version` goes too, for any caller without a base.
        const payload = {
          ...draft,
          version: item.version,
          base: baseFor(baseItem, draft),
        }
        if (Array.isArray(payload.cert_numbers)) {
          payload.cert_numbers = payload.cert_numbers.filter(Boolean)
        }
        if (forSale && acknowledged) payload.acknowledge_for_sale = true
        await api.updateInventoryItem(itemId, payload)
        setError('')
      } catch (err) {
        setError(err.message)
        setSaving(false)
        // Someone changed one of these fields between the last check and this
        // save: read the item again, and the conflicts show for a choice.
        if (err.body?.conflicts) reloadItem()
        return
      }
    }

    if (heldFriedberg?.action === 'attach') {
      const failed = await applyFriedberg(heldFriedberg)
      if (failed) problems.push(`the Friedberg number was not attached: ${failed}`)
    }
    if (errorsChanged) {
      try {
        await api.setItemErrors(itemId, errors, {
          acknowledgeForSale: forSale && acknowledged,
        })
        setSavedErrors(errors)
      } catch (err) {
        problems.push(`the errors were not recorded: ${err.message}`)
      }
    }
    const failedEdits = await applyPhotoEdits()
    if (failedEdits.length > 0) {
      problems.push(`photograph changes not saved: ${failedEdits.join('; ')}`)
    }
    const failedPhotos = await filePhotos()
    if (failedPhotos.length > 0) problems.push(photosFailed(failedPhotos))

    // Read the item back: the saved values, and the version the save made.
    // A form that stays open after a save -- the last item of a review, or
    // Receiving's one-item review -- would otherwise keep the old version and
    // the spent draft, and its next save would be refused as a conflict with
    // itself.
    try {
      adopt(await api.getInventoryItem(itemId))
    } catch (err) {
      problems.push(`saved, but could not read it back: ${err.message}`)
    } finally {
      setSaving(false)
    }
    if (problems.length > 0) {
      setError(`Not all was saved -- ${problems.join('; ')}`)
      return
    }
    setError('')
    onSaved?.()
  }

  return (
    <div className="edit-form">
      <HelpScope>
        <div className="row">
          <h2>{item.item_code}</h2>
          {item.parent_item_code && (
            <span className="muted">split from {item.parent_item_code}</span>
          )}
          {item.piece_codes?.length > 0 && (
            <span className="muted">split into {item.piece_codes.join(', ')}</span>
          )}
          {item.purchase_order_id && (
            // A new tab, so the editor and the search behind it stay as they
            // are. A plain link, not a routed one: the editor is also opened
            // where no router is mounted. The console's pages live under
            // /management (management/main.jsx's basename).
            <a
              href={`/management/purchases?order=${item.purchase_order_id}`}
              target="_blank"
              rel="noopener noreferrer"
              data-help="item_purchase"
            >
              {/* Named for what it opens: the order number is the purchase's,
                  changed there, not on the item. */}
              Purchase: {item.order_number ?? 'no order number'}
              {item.vendor ? ` · ${item.vendor}` : ''} -- edit
            </a>
          )}
          {item.sellers_item_id && (
            // The listing it was bought from, on eBay (app.ebay_orders).
            <a
              href={`https://www.ebay.com/itm/${item.sellers_item_id}`}
              target="_blank"
              rel="noopener noreferrer"
              data-help="sellers_item_id"
            >
              eBay item {item.sellers_item_id}
            </a>
          )}
          {/^https?:\/\//i.test(item.listing_url ?? '') && (
            // Only a web address is offered as a link.
            <a
              href={item.listing_url}
              target="_blank"
              rel="noopener noreferrer"
              data-help="listing_url"
            >
              Listing
            </a>
          )}
          {onClose && (
            <button className="link" onClick={onClose}>
              Close
            </button>
          )}
        </div>

        {error && <p className="error">{error}</p>}
        {splitNote && (
          <p className="muted" role="status">
            {splitNote}
          </p>
        )}
        {item.split_at && (
          <p className="muted">
            This lot has been split into its pieces. It is kept for its purchase and
            item code, but is no longer counted: edit the pieces instead.
          </p>
        )}

        {refreshedAt && conflicts.length === 0 && (
          <p className="muted" role="status">
            Updated with changes made elsewhere at {refreshedAt.toLocaleTimeString()}.
          </p>
        )}
        {conflicts.length > 0 && (
          <ConflictList
            item={item}
            draft={draft}
            conflicts={conflicts}
            // Keep mine: the other change has been seen, so mine is now based
            // on it and will replace it.
            onKeepMine={(key) => setBaseItem((base) => ({ ...base, [key]: item[key] }))}
            onUseTheirs={(key) => {
              setDraft(({ [key]: _dropped, ...rest }) => rest)
              setBaseItem((base) => ({ ...base, [key]: item[key] }))
            }}
          />
        )}
        <ForSaleNotice
          uses={item.sale_state ?? []}
          checked={acknowledged}
          onChange={setAcknowledged}
          action={
            endsOffer ? 'Change the status and end the offer' : 'Change it anyway'
          }
        />
        {/* A new status (or disposition) takes an offered item off sale:
            the save ends its offer. Said before the box is ticked, not
            discovered afterwards. */}
        {forSale && endsOffer && (
          <p className="error">
            Changing its status takes it off sale: saving ends its offer, and the
            listing will no longer be shown to buyers.
          </p>
        )}

        {TEXT_FIELDS.map(([label, key, letter]) => (
          <label key={key} className="field" data-help={key}>
            <AccessLabel text={label} accessKey={letter} />
            <input
              type="text"
              value={value(key)}
              onChange={set(key)}
              {...accel(letter)}
            />
            {claim(key)}
            {review(REVIEWABLE[key])}
          </label>
        ))}
        {/* Outside the label: a button inside a label with no `for`
            takes the label from its input. */}
        <div className="row">
          <button type="button" className="link" onClick={suggestDescription}>
            Suggest description
          </button>
          <span className="muted">{suggestNote}</span>
        </div>

        {/* The seller's listing id -- eBay's item number. Filled from the
            listing link by app.ebay_orders where there was one; typed here
            for the rest. The link above opens the listing. */}
        <label className="field" data-help="sellers_item_id">
          <span>Seller&apos;s item id</span>
          <input
            type="text"
            value={value('sellers_item_id') || ''}
            onChange={set('sellers_item_id')}
          />
          <span />
          <span />
        </label>
        {/* Where it is kept: a move is kept in its location history. The
            server asks no for-sale acknowledgement for a move alone -- no
            buyer sees it -- but this form's Save waits for the tick on any
            change. */}
        <label className="field" data-help="storage_location_id">
          <span>Storage location</span>
          <LocationSelect
            value={
              value('storage_location_id') == null
                ? ''
                : String(value('storage_location_id'))
            }
            onChange={(id) =>
              setDraft({ ...draft, storage_location_id: id ? Number(id) : null })
            }
          />
          <span />
          <span />
        </label>
        <label className="field" data-help="listing_url">
          <span>Listing web address</span>
          <input
            type="url"
            placeholder="https://"
            value={value('listing_url') || ''}
            onChange={set('listing_url')}
          />
          <span />
          <span />
        </label>
        {/* Divs, not labels: a <label> may not contain the range checkbox's
          own label, so each box is named through htmlFor instead.

          One row that stays mounted, with the end year added beneath it.
          Two separate layouts would replace the checkbox itself on every
          tick, and a keyboard user's focus would go with it.

          Not on a note: its year is its series year, and the server leaves
          the item's own years empty. */}
        {!isCurrencyKind(value('item_kind')) && (
          <>
            <div className="field" data-help={ranged ? 'year_start' : 'year'}>
              <label htmlFor={yearId}>
                <AccessLabel text={ranged ? 'Year from' : 'Year'} accessKey="y" />
              </label>
              <span className="year-input">
                <input
                  id={yearId}
                  type="number"
                  disabled={noDate}
                  value={value('year_start')}
                  onChange={
                    ranged
                      ? (e) =>
                          setDraft({ ...draft, year_start: yearValue(e.target.value) })
                      : setYear
                  }
                  {...accel('y')}
                />
                {rangeToggle}
              </span>
              {ranged ? claim('year_start') : yearClaim()}
              {review(ranged ? 'year_start' : ['year_start', 'year_end'])}
            </div>
            {yearWarning && (
              <p className="notice" role="status">
                {yearWarning}
              </p>
            )}
            {ranged && (
              <div className="field" data-help="year_end">
                <label htmlFor={yearEndId}>
                  <AccessLabel text="Year to" accessKey="o" />
                </label>
                <input
                  id={yearEndId}
                  type="number"
                  // An item stored with a start and no end opens its range at the
                  // start year -- but only until someone types here.
                  value={
                    'year_end' in draft
                      ? (draft.year_end ?? '')
                      : (item.year_end ?? item.year_start ?? '')
                  }
                  onChange={(e) =>
                    setDraft({ ...draft, year_end: yearValue(e.target.value) })
                  }
                  {...accel('o')}
                />
                {claim('year_end')}
                {review('year_end')}
              </div>
            )}
          </>
        )}

        {NUMBER_FIELDS.map(([label, key, letter]) => (
          <label key={key} className="field" data-help={key}>
            <AccessLabel text={label} accessKey={letter} />
            <input
              type="number"
              value={value(key)}
              onChange={set(key)}
              {...accel(letter)}
            />
            {claim(key)}
            {review(REVIEWABLE[key])}
          </label>
        ))}

        {MONEY_FIELDS.map(([label, key, letter]) => (
          <label key={key} className="field" data-help={key}>
            <AccessLabel text={label} accessKey={letter} />
            {/* Text, not number. Money crosses the API as a string and a number
              input would hand back a float, which is the one thing this
              schema is careful never to do. */}
            <input
              type="text"
              inputMode="decimal"
              value={value(key)}
              onChange={set(key)}
              {...accel(letter)}
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

        {item.tax_rate !== undefined && (
          <div className="field">
            Sales tax
            <span title="Recalculated by the database when saved">
              {money(item.sales_tax)}
            </span>
            <label className="checkbox" data-help="no_sales_tax">
              <input
                type="checkbox"
                checked={untaxed}
                onChange={toggleTax}
                {...accel('n')}
              />
              {/* */}
              <AccessLabel text="No sales tax charged" accessKey="n" />
            </label>
            <span />
          </div>
        )}

        {/* The kind decides which fields follow: choosing another shows its
            fields at once, and empties the ones it cannot have. */}
        <label className="field" data-help="item_kind">
          <span>Kind</span>
          <ReferenceSelect
            table="item_kind"
            value={value('item_kind')}
            onChange={changeKind}
            allowAdd={false}
            allowBlank={false}
          />
          {side('item_kind', 'item_kind_id')}
          <span />
        </label>
        {Object.keys(kindCleared).length > 0 && (
          <p className="for-sale" role="status">
            As {kindLabel(value('item_kind'))} it cannot keep{' '}
            {Object.entries(kindCleared)
              .map(([key, { was }]) => `${fieldName(key)} (${shown(was)})`)
              .join(', ')}
            : saving clears them. Choose {kindLabel(item.item_kind)} again to keep them.
          </p>
        )}
        {leavingNote && item.friedberg_id && pendingFriedberg?.action !== 'clear' && (
          <p className="error">
            It has a Friedberg number: clear it below before saving, or the save is
            refused.
          </p>
        )}

        {/* A note's own facts come right after its kind, as on New item: its
            series, serial and plates identify it. The draft's kind, not the
            saved one: a coin being made a note
            shows its note fields now, and the save creates the note's row
            before writing them. */}
        {value('item_kind') === 'currency' && (
          <NoteFields
            value={value}
            set={set}
            setNumber={(key) => (e) =>
              setDraft({ ...draft, [key]: yearValue(e.target.value) })
            }
            setField={(key, next) => setDraft({ ...draft, [key]: next })}
            side={side}
          />
        )}

        {CLASSIFIERS.filter(([, key]) => fieldFitsKind(key, value('item_kind'))).map(
          ([label, key, table, letter]) => (
            <label key={key} className="field" data-help={key}>
              <AccessLabel text={label} accessKey={letter} />
              <ReferenceSelect
                table={table}
                value={value(key)}
                onChange={set(key)}
                allowAdd={!FIXED_VOCABULARIES.has(table)}
                // Status is NOT NULL on the item, so there is no blank to pick:
                // clearing it would be a 422 the operator cannot act on.
                allowBlank={key !== 'status'}
                filter={
                  key === 'grade'
                    ? (grade) => gradeFitsKind(grade, value('item_kind'))
                    : key === 'denomination' || key === 'grade_designation'
                      ? (entry) => fitsKind(entry, value('item_kind'))
                      : undefined
                }
                {...accel(letter)}
              />
              {side(key, columnOf(key, true))}
              {review(REVIEWABLE[key])}
            </label>
          ),
        )}

        {fieldFitsKind('variety', value('item_kind')) && (
          <label className="field" data-help="variety">
            <span>Variety</span>
            <input value={value('variety')} onChange={set('variety')} />
            {side('variety', 'variety')}
            <span />
          </label>
        )}

        <label className="field" data-help="cert_numbers">
          <span>Certificate no.</span>
          <input
            value={(value('cert_numbers') || []).join(', ')}
            onChange={(e) =>
              setDraft({ ...draft, cert_numbers: certsFrom(e.target.value) })
            }
          />
          {side('cert_numbers', 'cert_numbers')}
          <span />
        </label>

        <AttributesField
          item={item}
          kind={value('item_kind')}
          codes={
            'attributes' in draft
              ? draft.attributes
              : (item.attributes ?? []).map((a) => a.code)
          }
          onChange={(codes) => setDraft({ ...draft, attributes: codes })}
        />

        {/* Held here and recorded by Save, like every other edit on this
          form. No sale state goes to the panel: this form's own notice
          above is the one acknowledgement, and it covers the errors too. */}
        {errors === null ? (
          <div className="field errors-panel">
            <span>Errors</span>
            {errorsReadError ? (
              <p className="error">The errors could not be read: {errorsReadError}</p>
            ) : (
              <p className="muted">Loading...</p>
            )}
            <span />
            <span />
          </div>
        ) : (
          <ErrorsPanel
            itemId={null}
            kind={value('item_kind')}
            value={errors}
            onChange={setErrors}
            saleState={[]}
          />
        )}

        {/* A photograph added here, and a new role, primary, removal or move
          for one already filed, is held until this form's Save applies it, under
          this form's one for-sale acknowledgement. */}
        <PhotosPanel
          itemId={itemId}
          pending={pendingPhotos}
          edits={photoEdits}
          onEditsChange={setPhotoEdits}
          reloadKey={photoKey}
          onAdd={(entry) =>
            setPendingPhotos((list) => [
              ...list,
              { ...entry, key: crypto.randomUUID() },
            ])
          }
          onRoleChange={(key, role) =>
            setPendingPhotos((list) =>
              list.map((entry) =>
                entry.key === key ? { ...entry, role, error: undefined } : entry,
              ),
            )
          }
          onDiscard={(key) =>
            setPendingPhotos((list) => list.filter((entry) => entry.key !== key))
          }
        />

        {/* The saved kind: a Friedberg number hangs on the note's stored row,
            so it can be looked up only once that row exists -- and cleared
            before the note stops being one. */}
        {item.item_kind === 'currency' && (
          <FriedbergPanel
            item={item}
            pending={pendingFriedberg}
            onHold={setPendingFriedberg}
            onUndo={() => setPendingFriedberg(null)}
          />
        )}

        <div className="row">
          <SaveButton saving={saving} disabled={!canSave} onClick={save} />
          {/* A lot is split as saved: the pieces copy the stored record, so
              an edit still waiting here would not reach them. Neither a
              split lot nor a piece of one can be split again. */}
          {!item.split_at && !item.parent_item_id && (
            <>
              <button
                type="button"
                disabled={Object.keys(draft).length > 0}
                onClick={() => setSplitting(true)}
              >
                Split into pieces...
              </button>
              {Object.keys(draft).length > 0 && (
                <span className="muted">Save or undo your changes to split it.</span>
              )}
            </>
          )}
        </div>
        {splitting && (
          <SplitDialog
            item={item}
            onClose={() => setSplitting(false)}
            onSplit={(result) => {
              setSplitting(false)
              setSplitNote(
                `Split into ${result.pieces.length} pieces: ` +
                  `${result.pieces.map((piece) => piece.item_code).join(', ')}. ` +
                  `They cost ${result.allocated_total_cost} in all; the lot cost ` +
                  `${result.parent_total_cost}.`,
              )
              reloadItem()
              // Not `onSaved`, which closes the editor over the results: what
              // the split made is said here, while the list behind catches up.
              onChanged?.()
            }}
          />
        )}

        {/* What is being asked for the item, beside what it has sold for.
          Self-loading like the sale history below, and it writes nothing itself:
          starting and ending an offer both go through the offers API, which
          is the only thing allowed to set a listing's status. */}
        <OffersPanel item={item} onChanged={reloadItem} />

        <SaleHistory itemId={itemId} />

        {/* Read-only; re-read whenever the item's version moves, so a save
            made above appears here at once. */}
        <HistoryPanel itemId={itemId} version={item.version} />
      </HelpScope>
    </div>
  )
}
