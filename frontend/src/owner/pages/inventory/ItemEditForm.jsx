import { useEffect, useId, useRef, useState } from 'react'

import { api } from '../../api'
import { fieldFitsKind, fitsKind, sideFor } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import { AccessLabel } from '../../AccessLabel'
import { accel, useSaveShortcut } from '../../shortcuts'
import ForSaleNotice from '../ForSaleNotice'
import ErrorsPanel from './ErrorsPanel'
import { baseFor, conflictsOf, fieldValue, rebase } from './fieldMerge'
import FriedbergPanel from './FriedbergPanel'
import HelpScope from '../../HelpScope'
import { FIELD_HELP } from '../../fieldHelp'
import OffersPanel from './OffersPanel'
import PhotosPanel from './PhotosPanel'

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

/** A field's name for the conflict list: its help title, else its key. */
const fieldName = (key) => FIELD_HELP[key]?.title ?? key.replaceAll('_', ' ')

/** A value as the conflict list shows it. */
function shown(value) {
  if (value === null || value === undefined || value === '') return '(blank)'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '(none)'
  return String(value)
}

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
 * only to an era -- 13 items of 7,658 -- so the form asks for one year unless
 * the item already has a range. Opening a stored range as one would let a
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
  ['Denomination', 'denomination', 'denomination', 'm'],
  ['Country', 'country', 'country', 'u'],
  ['Metal', 'metal', 'metal', 'l'],
  // Status is editable here because Receiving only moves an item forward.
  // Nothing else could put one back: a parcel recorded as received in error,
  // or against the wrong row, had no way home. `PATCH /api/inventory/{id}`
  // has always accepted it and routes it through `set_status`, so the
  // status-history row is written either way -- an item's history stays a
  // true account of where it has been, including the correction.
  ['Status', 'status', 'item_status', 's'],
]

//: A note's own fields, shown only for a banknote. Letters are scarce by
//: here: only these two labels hold one that is still free.
const NOTE_CLASSIFIERS = [
  ['Note class', 'note_type', 'note_type', 'a'],
  ['Seal', 'seal_color', 'seal_color', null],
  ['Signatures', 'signature_combination', 'signature_combination', null],
  ['Reserve Bank', 'fed_district', 'fed_district', 'b'],
]

const NOTE_TEXT_FIELDS = [
  ['Series year', 'series_year', 'number'],
  ['Series letter', 'series_letter', 'text'],
  ['Serial number', 'serial_number', 'text'],
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

//: Where a derived attribute was read, as its mark's tooltip says it.
const ATTRIBUTE_FROM = {
  serial_pattern: 'Read from the serial number',
  import: 'Read from the spreadsheet',
  rating: 'Read from the rating',
  attribute_rule: "Follows from the note's class and series",
}

//: `item_attribute.attribute_group`'s members, for the add form's group
//: picker. The reference API has no endpoint listing an enum's values --
//: only each existing row's own `extra.attribute_group` -- so this mirrors
//: `AttributeGroup` in backend/app/models/reference.py by hand; keep the two
//: in step if that enum changes.
const ATTRIBUTE_GROUPS = [
  { code: 'serial', label: 'Serial' },
  { code: 'variety', label: 'Variety' },
  { code: 'release', label: 'Release' },
  { code: 'verification', label: 'Verification' },
  { code: 'qualifier', label: 'Qualifier' },
]

/**
 * What the item is beyond its grade: Star Note, No Motto, First Strike.
 *
 * The whole set is saved with the item, so a stale form is a 409 like any
 * other field. Removing one a rule read (marked "read") keeps it removed:
 * the rule does not add it back. Only attributes for this kind of item are
 * offered -- a star note is not a coin's.
 */
function AttributesField({ item, codes, onChange, kind }) {
  const vocabulary = useReference('item_attribute') ?? []
  const byCode = new Map(vocabulary.map((entry) => [entry.code, entry]))
  const held = new Map((item.attributes ?? []).map((a) => [a.code, a]))

  return (
    <div className="field">
      <span>Attributes</span>
      <div className="attribute-list">
        {codes.map((code) => {
          const label = byCode.get(code)?.label ?? held.get(code)?.label ?? code
          const read = held.get(code)?.source === 'derived' ? held.get(code) : null
          return (
            <span key={code} className="chip alias-chip">
              {label}
              {read && (
                <span
                  className="suggested"
                  title={ATTRIBUTE_FROM[read.derived_by] ?? 'Read by a rule'}
                >
                  read
                </span>
              )}
              <button
                type="button"
                aria-label={`Remove ${label}`}
                onClick={() => onChange(codes.filter((c) => c !== code))}
              >
                ×
              </button>
            </span>
          )
        })}
        {/* Only once loaded: until then the picker is a text box, and each
            keystroke would add a partial code. */}
        {vocabulary.length > 0 && (
          <ReferenceSelect
            table="item_attribute"
            value=""
            allowAdd
            labelOnly
            // No top-level `applies_to` field: the API reads it from `extra`
            // (`ReferenceValueCreate`). A value added with none would fit no
            // kind and vanish from this very picker the moment it appeared
            // -- see `fitsKind`, whose exact inverse `sideFor` is.
            // `attribute_group` is NOT NULL with no database default, and the
            // owner chose to ask rather than have one picked silently:
            // `groupField`/`groupOptions` put a required group picker in the
            // add form instead.
            addFields={{ applies_to: sideFor(kind) }}
            groupField="attribute_group"
            groupOptions={ATTRIBUTE_GROUPS}
            filter={(entry) => fitsKind(entry, kind) && !codes.includes(entry.code)}
            onChange={(e) => {
              if (e.target.value) onChange([...codes, e.target.value])
            }}
          />
        )}
      </div>
      <span />
      <span />
    </div>
  )
}

/**
 * Every sale of the item, each as it was sold.
 *
 * A returned item may be corrected and sold again; each sale keeps the
 * item's name, grade and price from the day it sold.
 *
 * A sale made inside a lot is shown as this coin's own share of the line,
 * not the line's `quantity` and `unit_price` -- those are the whole group's,
 * so a three-coin lot sold for 1,000.00 would otherwise claim the full
 * 1,000.00 against each of its coins on the one screen that answers "what
 * happened to this coin".
 */
function SaleHistory({ itemId }) {
  const [sales, setSales] = useState(null)

  useEffect(() => {
    let cancelled = false
    api
      .getItemSales(itemId)
      .then((body) => {
        if (!cancelled) setSales(body)
      })
      .catch(() => {
        if (!cancelled) setSales([])
      })
    return () => {
      cancelled = true
    }
  }, [itemId])

  if (!sales || sales.length === 0) return null
  return (
    <div className="sale-history">
      <h3>Sales</h3>
      <ul>
        {sales.map((sale) => {
          const sold = sale.snapshot?.item
          const inLot = sale.sales_lot_id != null
          return (
            <li key={`${sale.order_id}-${sale.placed_at}`}>
              Order #{sale.order_id}, {sale.placed_at.slice(0, 10)}, {sale.status}:{' '}
              {inLot
                ? `${sale.share_amount ?? sale.unit_price} of lot #${sale.sales_lot_id}`
                : `${sale.quantity} at ${sale.unit_price}`}{' '}
              to {sale.customer_name}
              {sold && (
                <span className="muted">
                  {' '}
                  -- sold as {sold.source_title}
                  {sold.grade_display ? `, ${sold.grade_display}` : ''}
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** The column a form field is stored in, as `derived` and reviews name it. */
const columnOf = (key, isClassifier) => (isClassifier ? `${key}_id` : key)

/** `accel` attributes, or none for a field that has no letter. */
const keys = (letter) => (letter ? accel(letter) : {})

//: Vocabularies this form must not let anyone extend. `item_status` is a
//: lifecycle the code branches on, not a descriptive list that grows with
//: use -- see `ReferenceSelect`'s `allowAdd`.
const FIXED_VOCABULARIES = new Set(['item_status', 'strike_type'])

export default function ItemEditForm({ itemId, onSaved, onClose }) {
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
  const [ranged, setRanged] = useState(false)
  // Ticked to change an item that is for sale; reset whenever it is loaded.
  const [acknowledged, setAcknowledged] = useState(false)
  const yearId = useId()
  const yearEndId = useId()

  // `save` is a function declaration below, hoisted for the whole component
  // scope, so it is safe to reference here even though it is defined later --
  // this hook must sit above every early return.
  const forSale = (item?.sale_state ?? []).length > 0
  // What the server ends an offer for: a status or disposition that differs
  // from the one the item holds.
  const endsOffer = ['status', 'disposition'].some(
    (key) => key in draft && draft[key] !== item?.[key],
  )
  // Fields edited here that someone else has changed since: each waits for a
  // choice before the form can be saved.
  const conflicts = item && baseItem ? conflictsOf(item, baseItem, draft) : []
  const canSave =
    !saving &&
    Object.keys(draft).length > 0 &&
    (!forSale || acknowledged) &&
    conflicts.length === 0
  useSaveShortcut(save, canSave)

  useEffect(() => {
    let cancelled = false
    api
      .getInventoryItem(itemId)
      .then((body) => {
        if (cancelled) return
        setItem(body)
        setBaseItem(body)
        setReviewed(body.reviewed ?? [])
        setRanged(isRange(body.year_start, body.year_end))
        setDraft({})
        setAcknowledged(false)
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
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
  // to resolve rather than being overwritten or adopted unseen (owner's
  // request, 2026-09-23).
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
        // them shows as a conflict, never silently adopted -- which is what
        // taking the new version wholesale used to do (code review,
        // 2026-09-23).
        setBaseItem((previous) => rebase(body, previous, draftRef.current))
      })
      .catch((err) => setError(err.message))
  }

  if (error && !item) return <p className="error">{error}</p>
  if (!item) return <p className="muted">Loading...</p>

  // `in`, not `??`: a year cleared in the draft is null, and must not fall
  // back to showing the stored year it is about to replace.
  const value = (key) => (key in draft ? draft[key] : item[key]) ?? ''
  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

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
      <label className="review-mark" title="I have confirmed this by examination">
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

  const rangeToggle = (
    <label className="checkbox">
      <input type="checkbox" checked={ranged} onChange={toggleRange} {...accel('r')} />
      {/* */}
      <AccessLabel text="Range of years" accessKey="r" />
    </label>
  )

  async function save() {
    setSaving(true)
    try {
      // `base` makes the save field by field: a change made elsewhere since
      // stops it only where it touched a field changed here (409 naming
      // them). `version` goes too, for any caller without a base.
      const payload = {
        ...draft,
        version: item.version,
        base: baseFor(baseItem, draft),
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
    // Read the item back: the saved values, and the version the save made.
    // A form that stays open after a save -- the last item of a review, or
    // Receiving's one-item review -- otherwise kept the old version and the
    // spent draft, and its next save was refused as a conflict with itself
    // (code review, 2026-09-23).
    try {
      const fresh = await api.getInventoryItem(itemId)
      setItem(fresh)
      setBaseItem(fresh)
      setReviewed(fresh.reviewed ?? [])
      setRanged(isRange(fresh.year_start, fresh.year_end))
      setDraft({})
      setAcknowledged(false)
    } catch (err) {
      setError(`Saved, but could not read it back: ${err.message}`)
    } finally {
      setSaving(false)
    }
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
          {onClose && (
            <button className="link" onClick={onClose}>
              Close
            </button>
          )}
        </div>

        {error && <p className="error">{error}</p>}

        {refreshedAt && conflicts.length === 0 && (
          <p className="muted" role="status">
            Updated with changes made elsewhere at {refreshedAt.toLocaleTimeString()}.
          </p>
        )}
        {conflicts.length > 0 && (
          <div className="for-sale" role="alert">
            <strong>Changed elsewhere while you were editing</strong>
            <ul>
              {conflicts.map((key) => (
                <li key={key}>
                  {fieldName(key)}: now {shown(fieldValue(item, key))}; yours{' '}
                  {shown(draft[key])}.{' '}
                  <button
                    type="button"
                    onClick={() =>
                      // Keep mine: the other change has been seen, so mine is
                      // now based on it and will replace it.
                      setBaseItem((base) => ({ ...base, [key]: item[key] }))
                    }
                  >
                    Keep mine
                  </button>{' '}
                  <button
                    type="button"
                    onClick={() => {
                      setDraft(({ [key]: _dropped, ...rest }) => rest)
                      setBaseItem((base) => ({ ...base, [key]: item[key] }))
                    }}
                  >
                    Use theirs
                  </button>
                </li>
              ))}
            </ul>
          </div>
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
          <label key={key} className="field">
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

        {/* Divs, not labels: a <label> may not contain the range checkbox's
          own label, so each box is named through htmlFor instead.

          One row that stays mounted, with the end year added beneath it.
          Two separate layouts replaced the checkbox itself on every tick, and
          a keyboard user's focus went with it. */}
        <div className="field">
          <label htmlFor={yearId}>
            <AccessLabel text={ranged ? 'Year from' : 'Year'} accessKey="y" />
          </label>
          <span className="year-input">
            <input
              id={yearId}
              type="number"
              value={value('year_start')}
              onChange={
                ranged
                  ? (e) => setDraft({ ...draft, year_start: yearValue(e.target.value) })
                  : setYear
              }
              {...accel('y')}
            />
            {rangeToggle}
          </span>
          {ranged ? claim('year_start') : yearClaim()}
          {review(ranged ? 'year_start' : ['year_start', 'year_end'])}
        </div>
        {ranged && (
          <div className="field">
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

        {NUMBER_FIELDS.map(([label, key, letter]) => (
          <label key={key} className="field">
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
          <label key={key} className="field">
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
              {`$${item.sales_tax}`}
            </span>
            <label className="checkbox">
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
                // Paper money is graded on its own scale: a note is offered only
                // note grades, and anything else only the coin scales.
                filter={
                  key === 'grade'
                    ? (grade) =>
                        (grade.extra?.grade_scale === 'note') ===
                        (value('item_kind') === 'currency')
                    : key === 'denomination'
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

        {/* Self-loading and self-saving: it fetches and PUTs its own set
          against this item, independent of the Save button above -- an
          error recorded here is not held back by, or lost to, a discarded
          edit elsewhere on this form. */}
        <ErrorsPanel
          itemId={itemId}
          kind={value('item_kind')}
          saleState={item.sale_state ?? []}
        />

        {/* Self-loading and self-saving, the same as the errors panel above:
          a photograph attached, re-roled or removed here is independent of
          the form's own Save. This is the only moment other than receiving
          that an item can gain a photograph -- see PhotosPanel's docstring. */}
        <PhotosPanel itemId={itemId} saleState={item.sale_state ?? []} />

        {item.item_kind === 'currency' && (
          <>
            {NOTE_CLASSIFIERS.map(([label, key, table, letter]) => (
              <label key={key} className="field" data-help={key}>
                {letter ? (
                  <AccessLabel text={label} accessKey={letter} />
                ) : (
                  <span>{label}</span>
                )}
                <ReferenceSelect
                  table={table}
                  value={value(key)}
                  onChange={set(key)}
                  allowAdd={false}
                  {...keys(letter)}
                />
                {side(key, columnOf(key, true))}
                <span />
              </label>
            ))}
            {NOTE_TEXT_FIELDS.map(([label, key, type]) => (
              <label key={key} className="field" data-help={key}>
                <span>{label}</span>
                <input
                  type={type}
                  value={value(key)}
                  onChange={
                    type === 'number'
                      ? (e) => setDraft({ ...draft, [key]: yearValue(e.target.value) })
                      : set(key)
                  }
                />
                {side(key, key)}
                <span />
              </label>
            ))}
            <FriedbergPanel item={item} onChanged={reloadItem} />
          </>
        )}

        <div className="row">
          <button disabled={!canSave} onClick={save} {...accel('v')}>
            <AccessLabel text={saving ? 'Saving...' : 'Save'} accessKey="v" />
          </button>
        </div>

        {/* What is being asked for the item, beside what it has sold for.
          Self-loading like the errors panel, and it writes nothing itself:
          starting and ending an offer both go through the offers API, which
          is the only thing allowed to set a listing's status. */}
        <OffersPanel item={item} onChanged={reloadItem} />

        <SaleHistory itemId={itemId} />
      </HelpScope>
    </div>
  )
}
