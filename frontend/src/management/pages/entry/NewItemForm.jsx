import { useEffect, useId, useRef, useState } from 'react'

import { api } from '../../api'
import { fieldFitsKind, fitsKind, isCurrencyKind } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { AccessLabel } from '../../AccessLabel'
import { accel, useSaveShortcut } from '../../shortcuts'
import { isMoney } from '../orders/cents'
import ErrorsPanel from '../inventory/ErrorsPanel'
import { withSuggestions, without } from './suggestions'

/**
 * One coin, banknote or lot bought on a purchase.
 *
 * Mirrors `POST /api/inventory`: classifier fields cross as codes through
 * `ReferenceSelect`, money as decimal-string text, and the coin/banknote
 * detail blocks are mutually exclusive by kind, exactly as the backend
 * rejects sending both at once.
 *
 * A purchase is rarely one item, so "Save and add another" exists beside
 * "Save": it keeps the exact fields listed in `SHARED_ON_REPEAT` -- kind,
 * status, country, denomination, series, series year/letter, seal, district,
 * note type, grading service, metal and mint -- and clears everything that
 * varies piece to piece (title, description, year, grade, serial number,
 * certificate, cost, shipping, piece count), then focuses the title box for
 * the next one. Plain "Save" clears the whole form -- the next item is not
 * assumed to be like this one.
 */

//: Kept across "Save and add another"; everything else in BLANK is cleared.
//: This exact set is a controller ruling (see the "Repeated entry" row in
//: `docs/specs/entry-panels-design.md`), not a guess at what "feels shared":
//: `grade`, `grade_designation`, `serial_number`, `cert_number`, `variety`,
//: `item_cost`, `shipping_cost` and `piece_count` are per-piece and always
//: clear, even though some of them often repeat in practice.
const SHARED_ON_REPEAT = [
  'item_kind',
  // The pieces entered one after another usually came from one listing.
  'sellers_item_id',
  'status',
  'country',
  'denomination',
  'series',
  'series_year',
  'series_letter',
  'seal_color',
  'fed_district',
  'note_type',
  'signature_combination',
  'grading_service',
  'metal',
  'mint',
]

const BLANK = {
  item_kind: 'coin',
  source_title: '',
  sellers_item_id: '',
  description: '',
  year_start: '',
  year_end: '',
  piece_count: '1',
  item_cost: '',
  shipping_cost: '',
  status: 'ordered',
  country: '',
  denomination: '',
  set_form: '',
  strike_type: '',
  grade: '',
  grade_designation: '',
  grading_service: '',
  cert_number: '',
  metal: '',
  series: '',
  mint: '',
  variety: '',
  serial_number: '',
  face_plate_number: '',
  back_plate_number: '',
  printing_facility: '',
  series_year: '',
  series_letter: '',
  seal_color: '',
  fed_district: '',
  note_type: '',
  signature_combination: '',
}

//: Fields the facts can fill in, by kind. What the form fills is marked as a
//: suggestion and sent back as one; what the person picks is theirs.
const NOTE_SUGGESTED = [
  'note_type',
  'seal_color',
  'signature_combination',
  'fed_district',
]
const COIN_SUGGESTED = ['metal']
const NO_SUGGESTIONS = Object.fromEntries(
  [...NOTE_SUGGESTED, ...COIN_SUGGESTED].map((key) => [key, null]),
)

/** How long typing must pause before the facts are looked up again. */
const SUGGEST_DELAY_MS = 250

/** An emptied number box clears the year rather than sending "". */
const yearValue = (text) => (text === '' ? '' : text)

export default function NewItemForm({
  purchaseOrderId,
  defaults,
  onSaved,
  disabledReason = '',
}) {
  // The form and its suggestion marks change together, so they are one state:
  // `suggested` maps a field to the code the facts filled in, for as long as
  // the person has not changed it.
  const [entry, setEntry] = useState({ form: BLANK, suggested: {} })
  const { form, suggested } = entry
  const setForm = (next) =>
    setEntry((e) => ({
      ...e,
      form: typeof next === 'function' ? next(e.form) : next,
    }))
  const [ranged, setRanged] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  // The item does not exist yet, so ErrorsPanel holds no state of its own
  // (itemId={null}) -- this is the whole set, sent in one PUT once the item
  // is created.
  const [errors, setErrors] = useState([])
  // Set only when the item was created but its errors were not: the create
  // already happened, so the item exists on the server even though this form
  // still shows it as unsaved. Cleared on a successful Retry.
  const [errorSaveFailure, setErrorSaveFailure] = useState(null)
  // What the note lookup said when no issue matches the series, kept with the
  // facts it was said about: edit them and it is not shown for the new ones.
  const [issueWarning, setIssueWarning] = useState({ key: '', text: '' })
  // The line beside Suggest description: what it did, or why it could not.
  const [describeNote, setDescribeNote] = useState('')
  const titleRef = useRef(null)
  const yearId = useId()
  const yearEndId = useId()

  const isCurrency = isCurrencyKind(form.item_kind)

  // Picking a value, even the suggested one, makes it the person's. Clearing
  // the denomination leaves no facts to suggest from, so the form takes back
  // whatever it filled in.
  const set = (key) => (e) => {
    const next = e.target.value
    setEntry((current) => {
      const updated = {
        form: { ...current.form, [key]: next },
        suggested: without(current.suggested, key),
      }
      return key === 'denomination' && !next
        ? withSuggestions(updated, NO_SUGGESTIONS)
        : updated
    })
  }

  // The facts that decide the suggestions. Only the person's own picks are
  // sent with them: a value the form filled in must not narrow the next one.
  const fields = isCurrency ? NOTE_SUGGESTED : COIN_SUGGESTED
  const chosen = Object.fromEntries(
    fields.filter((k) => form[k] && !(k in suggested)).map((k) => [k, form[k]]),
  )
  const facts = isCurrency
    ? {
        denomination: form.denomination,
        series_year: form.series_year,
        series_letter: form.series_letter,
        serial_number: form.serial_number,
        ...chosen,
      }
    : { denomination: form.denomination, country: form.country, year: form.year_start }
  const factsKey = JSON.stringify([isCurrency, facts])

  useEffect(() => {
    const [currency, params] = JSON.parse(factsKey)
    if (!params.denomination) return undefined
    let cancelled = false
    const timer = setTimeout(() => {
      const ask = currency ? api.suggestNote : api.suggestCoin
      ask(params)
        .then((found) => {
          if (cancelled) return
          // The warning is a message, not a field to fill: kept apart so
          // `withSuggestions` never writes it into the form.
          const { warning, ...codes } = found ?? {}
          setEntry((current) => withSuggestions(current, codes))
          setIssueWarning({ key: factsKey, text: warning ?? '' })
        })
        // A suggestion is a convenience: a failed lookup leaves the form as
        // the person left it rather than interrupting them.
        .catch(() => {})
    }, SUGGEST_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [factsKey])

  /** The "suggested" mark beside a field the facts filled in. */
  const mark = (key) =>
    key in suggested && form[key] ? (
      <span className="suggested" title="Filled in from the facts entered">
        suggested
      </span>
    ) : null

  // One year is both ends, as in the item editor: sending both is what keeps
  // an item with a start and no end reading as one year.
  function setYear(e) {
    const year = yearValue(e.target.value)
    setForm((f) => ({ ...f, year_start: year, year_end: year }))
  }

  function toggleRange(e) {
    setRanged(e.target.checked)
    if (e.target.checked) return
    setForm((f) => ({ ...f, year_end: f.year_start }))
  }

  // The coin scales and the note scale do not share values (MS64 means
  // nothing for a banknote), so a grade picked under one kind is cleared
  // rather than carried over, silently wrong, to the other. Only that
  // boundary matters: coin, bullion and medal all read the same scale, and
  // clearing between them would throw away a grade for nothing.
  function setKind(e) {
    const kind = e.target.value
    setForm((f) => {
      const sameSide = isCurrencyKind(kind) === isCurrencyKind(f.item_kind)
      return {
        ...f,
        item_kind: kind,
        grade: sameSide ? f.grade : '',
        // Designations are one side's too: EPQ is a note's, DCAM a coin's.
        grade_designation: sameSide ? f.grade_designation : '',
        // A note has no strike type.
        strike_type: isCurrencyKind(kind) ? '' : f.strike_type,
      }
    })
  }

  /** The form's facts as `POST /api/inventory/suggested-description` takes them. */
  function draftFacts() {
    const pieces = Number(form.piece_count)
    const draft = {
      item_kind: form.item_kind,
      piece_count: Number.isInteger(pieces) && pieces >= 1 ? pieces : 1,
      errors,
    }
    if (!isCurrency && form.year_start !== '') {
      draft.year_start = Number(form.year_start)
      draft.year_end =
        form.year_end === '' ? Number(form.year_start) : Number(form.year_end)
    }
    for (const key of [
      'country',
      'denomination',
      'strike_type',
      'grade',
      'grade_designation',
      'grading_service',
      'series',
    ]) {
      if (form[key]) draft[key] = form[key]
    }
    const side = isCurrency
      ? ['serial_number', 'series_letter', 'note_type', 'seal_color']
      : ['metal', 'mint', 'variety']
    for (const key of side) if (form[key]) draft[key] = form[key]
    if (isCurrency && form.series_year !== '')
      draft.series_year = Number(form.series_year)
    return draft
  }

  async function suggestDescription() {
    try {
      const { description } = await api.suggestDraftDescription(draftFacts())
      if (!description) {
        setDescribeNote('Nothing entered yet to describe it from.')
        return
      }
      setForm((f) => ({ ...f, description }))
      setDescribeNote('Suggested from what is entered -- edit it before saving.')
    } catch (err) {
      setDescribeNote(err.message)
    }
  }

  /** The `ItemCreate` body, or a thrown `Error` naming the first bad field. */
  function buildPayload() {
    if (!form.source_title.trim()) {
      throw new Error('Title is required.')
    }
    if (!isCurrency && ranged && form.year_start === '' && form.year_end !== '') {
      throw new Error('Enter Year from, or clear Year to.')
    }
    const payload = {
      purchase_order_id: purchaseOrderId,
      item_kind: form.item_kind,
      source_title: form.source_title.trim(),
      description: form.description,
      ...(form.sellers_item_id.trim() && {
        sellers_item_id: form.sellers_item_id.trim(),
      }),
      piece_count: form.piece_count === '' ? 1 : Number(form.piece_count),
      status: form.status,
      // The purchase's tax defaults, resolved once for every item entered on
      // it -- not something this form asks about item by item.
      tax_rate: defaults?.tax_rate ?? null,
      tax_includes_shipping: defaults?.tax_includes_shipping ?? null,
    }

    // A note's year is its series year, set by the server from it; a Year
    // typed while this was a coin is not sent for it.
    if (!isCurrency && form.year_start !== '') {
      payload.year_start = Number(form.year_start)
      payload.year_end =
        form.year_end === '' ? Number(form.year_start) : Number(form.year_end)
    }

    for (const [key, label] of [
      ['item_cost', 'Item cost'],
      ['shipping_cost', 'Shipping'],
    ]) {
      if (form[key] === '') continue
      if (!isMoney(form[key])) {
        throw new Error(`${label} must be a money amount like 12.34.`)
      }
      payload[key] = form[key]
    }

    for (const key of [
      'country',
      'denomination',
      'strike_type',
      'grade',
      'grade_designation',
      'grading_service',
      'series',
    ]) {
      if (form[key]) payload[key] = form[key]
    }
    if (!isCurrency && form.set_form) payload.set_form = form.set_form
    if (form.cert_number) payload.cert_number = form.cert_number

    // Coin and currency detail never cross: sending both is a 422 naming the
    // field, so only the block that matches the kind is ever included.
    if (isCurrency) {
      if (form.serial_number) payload.serial_number = form.serial_number
      for (const key of [
        'face_plate_number',
        'back_plate_number',
        'printing_facility',
      ]) {
        if (form[key]) payload[key] = form[key]
      }
      if (form.series_year !== '') payload.series_year = Number(form.series_year)
      if (form.series_letter) payload.series_letter = form.series_letter
      if (form.seal_color) payload.seal_color = form.seal_color
      if (form.fed_district) payload.fed_district = form.fed_district
      if (form.note_type) payload.note_type = form.note_type
      if (form.signature_combination) {
        payload.signature_combination = form.signature_combination
      }
    } else {
      if (form.metal) payload.metal = form.metal
      if (form.mint) payload.mint = form.mint
      if (form.variety) payload.variety = form.variety
    }

    const accepted = Object.keys(suggested).filter(
      (key) => fields.includes(key) && payload[key],
    )
    if (accepted.length) payload.suggested = accepted

    return payload
  }

  /**
   * Clears the form -- errors included -- and reports the created item, the
   * way a completed save always finishes, whether that is the first attempt
   * or a Retry after the errors step failed once.
   */
  function finishSave(created, addAnother) {
    if (addAnother) {
      const kept = Object.fromEntries(SHARED_ON_REPEAT.map((k) => [k, form[k]]))
      // A kept suggestion stays a suggestion. The Bank came from the serial,
      // which is cleared, so it is dropped and asked for again.
      const marks = Object.fromEntries(
        Object.entries(suggested).filter(([k]) => k in kept && k !== 'fed_district'),
      )
      setEntry({
        form: {
          ...BLANK,
          ...kept,
          ...('fed_district' in suggested && { fed_district: '' }),
        },
        suggested: marks,
      })
      setRanged(false)
      titleRef.current?.focus()
    } else {
      setEntry({ form: BLANK, suggested: {} })
      setRanged(false)
    }
    // Errors are per-piece, like grade or a serial number -- never carried
    // into the next item, "add another" included.
    setErrors([])
    setDescribeNote('')
    onSaved?.(created)
  }

  async function submit(addAnother) {
    setSaving(true)
    try {
      const payload = buildPayload()
      const created = await api.createInventoryItem(payload)
      setError('')
      if (errors.length > 0) {
        try {
          await api.setItemErrors(created.id, errors)
        } catch (err) {
          // The item exists. Saying "failed" would be false and would invite
          // a second entry of the same item.
          setErrorSaveFailure({ itemId: created.id, itemCode: created.item_code })
          setError(
            `${created.item_code} was created, but its errors were not saved: ` +
              `${err.message}`,
          )
          return // keep the form and its errors on screen
        }
      }
      setErrorSaveFailure(null)
      finishSave(created, addAnother)
    } catch (err) {
      // Kept in place: a refusal here (a 422 naming a field, an unknown
      // purchase order) must not throw away what was typed.
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  /**
   * Re-sends just the errors PUT for an item already created by a failed
   * first attempt, then finishes the save. Always finishes as a plain Save:
   * the create already happened once, and there is no second "and add
   * another" click here to say the next item should reuse these fields.
   */
  async function retrySaveErrors() {
    if (!errorSaveFailure) return
    setSaving(true)
    try {
      const { itemId, itemCode } = errorSaveFailure
      await api.setItemErrors(itemId, errors)
      setError('')
      setErrorSaveFailure(null)
      finishSave({ id: itemId, item_code: itemCode }, false)
    } catch (err) {
      setError(
        `${errorSaveFailure.itemCode} was created, but its errors were not saved: ` +
          `${err.message}`,
      )
    } finally {
      setSaving(false)
    }
  }

  // A pending errors-save failure means the item was already created: Save
  // (or Save and add another) would call `createInventoryItem` again and
  // enter the same piece a second time. Retry is the only way forward until
  // the failure clears.
  const disabled = saving || Boolean(disabledReason) || Boolean(errorSaveFailure)

  useSaveShortcut(() => submit(false), !disabled && form.source_title.trim() !== '')

  return (
    <div className="admin-form">
      <h3>New item</h3>
      {error && <p className="error">{error}</p>}
      {errorSaveFailure && (
        <button type="button" onClick={retrySaveErrors} disabled={saving}>
          Retry
        </button>
      )}
      {disabledReason && <p className="error">{disabledReason}</p>}

      <div className="form-grid">
        <label data-help="item_kind">
          <AccessLabel text="Kind" accessKey="k" />
          <ReferenceSelect
            table="item_kind"
            value={form.item_kind}
            onChange={setKind}
            allowBlank={false}
            {...accel('k')}
          />
        </label>

        <label data-help="source_title">
          <AccessLabel text="Title" accessKey="t" />
          <input
            ref={titleRef}
            type="text"
            value={form.source_title}
            onChange={set('source_title')}
            {...accel('t')}
          />
        </label>

        <label data-help="sellers_item_id">
          Seller&apos;s item id
          <input
            type="text"
            value={form.sellers_item_id}
            onChange={set('sellers_item_id')}
          />
        </label>

        <label data-help="piece_count">
          <AccessLabel text="Pieces" accessKey="p" />
          <input
            type="number"
            min="1"
            value={form.piece_count}
            onChange={set('piece_count')}
            {...accel('p')}
          />
        </label>

        <label data-help="item_cost">
          <AccessLabel text="Item cost" accessKey="i" />
          <input
            type="text"
            inputMode="decimal"
            value={form.item_cost}
            onChange={set('item_cost')}
            {...accel('i')}
          />
        </label>

        <label data-help="shipping_cost">
          <AccessLabel text="Shipping" accessKey="h" />
          <input
            type="text"
            inputMode="decimal"
            value={form.shipping_cost}
            onChange={set('shipping_cost')}
            {...accel('h')}
          />
        </label>

        <label data-help="country">
          <AccessLabel text="Country" accessKey="u" />
          <ReferenceSelect
            table="country"
            value={form.country}
            onChange={set('country')}
            placeholder="US"
            {...accel('u')}
          />
        </label>

        <label data-help="denomination">
          <AccessLabel text="Denomination" accessKey="m" />
          <ReferenceSelect
            table="denomination"
            value={form.denomination}
            onChange={set('denomination')}
            // A note is offered only note denominations, and anything else
            // only the coin ones -- the same split the item editor applies.
            filter={(entry) => fitsKind(entry, form.item_kind)}
            // A face value with a currency and a side: not added by label.
            allowAdd={false}
            {...accel('m')}
          />
        </label>

        {fieldFitsKind('set_form', form.item_kind) && (
          <label data-help="set_form">
            Set form
            <ReferenceSelect
              table="set_form"
              value={form.set_form}
              onChange={set('set_form')}
            />
          </label>
        )}

        {/* A coin's grade is a number and its strike type says whether 65
            is MS65 or PR65. A note has none. No free accelerator letter is
            left in "Strike type".

            Which fields are a coin's is asked of `fieldFitsKind` rather than
            answered here, so this form and the item editor read one list
            (`COIN_ONLY_FIELDS`). The editor keeping a metal box this form had
            already dropped is what that shared list exists to stop. */}
        {fieldFitsKind('strike_type', form.item_kind) && (
          <label data-help="strike_type">
            Strike type
            <ReferenceSelect
              table="strike_type"
              value={form.strike_type}
              onChange={set('strike_type')}
              allowAdd={false}
            />
          </label>
        )}

        <label data-help="grade">
          <AccessLabel text="Grade" accessKey="g" />
          <ReferenceSelect
            table="grade"
            value={form.grade}
            onChange={set('grade')}
            // A note is offered only the paper-money scale, and anything
            // else only the coin scales -- the same filter the item editor
            // applies.
            filter={(grade) => (grade.extra?.grade_scale === 'note') === isCurrency}
            {...accel('g')}
          />
        </label>

        <label data-help="grade_designation">
          Grade designation
          <ReferenceSelect
            table="grade_designation"
            value={form.grade_designation}
            onChange={set('grade_designation')}
            // EPQ and PPQ for a note, DCAM and the rest for a coin -- the
            // item editor's filter.
            filter={(entry) => fitsKind(entry, form.item_kind)}
          />
        </label>

        <label data-help="grading_service">
          Grading service
          <ReferenceSelect
            table="grading_service"
            value={form.grading_service}
            onChange={set('grading_service')}
          />
        </label>

        <label data-help="cert_number">
          Certificate number
          <input type="text" value={form.cert_number} onChange={set('cert_number')} />
        </label>

        {fieldFitsKind('metal', form.item_kind) && (
          <label data-help="metal">
            <AccessLabel text="Metal" accessKey="l" />
            <ReferenceSelect
              table="metal"
              value={form.metal}
              onChange={set('metal')}
              {...accel('l')}
            />
            {mark('metal')}
          </label>
        )}

        <label data-help="series">
          <AccessLabel text="Series" accessKey="s" />
          <ReferenceSelect
            table="series"
            value={form.series}
            onChange={set('series')}
            filter={(entry) => fitsKind(entry, form.item_kind)}
            {...accel('s')}
          />
        </label>

        {/* Gated on Mint, the coin-only field of the pair: Variety is not in
            COIN_ONLY_FIELDS but has always been shown beside it, and moving
            it is a change to the form, not to this fix. */}
        {fieldFitsKind('mint', form.item_kind) && (
          <>
            <label data-help="mint">
              Mint
              <ReferenceSelect table="mint" value={form.mint} onChange={set('mint')} />
            </label>
            <label data-help="variety">
              Variety
              <input type="text" value={form.variety} onChange={set('variety')} />
            </label>
          </>
        )}

        {isCurrency && (
          <>
            <label data-help="serial_number">
              Serial number
              <input
                type="text"
                value={form.serial_number}
                onChange={set('serial_number')}
              />
            </label>
            <label data-help="face_plate_number">
              Face plate
              <input
                type="text"
                value={form.face_plate_number}
                onChange={set('face_plate_number')}
              />
            </label>
            <label data-help="back_plate_number">
              Back plate
              <input
                type="text"
                inputMode="numeric"
                value={form.back_plate_number}
                onChange={set('back_plate_number')}
              />
            </label>
            <label data-help="printing_facility">
              Printed at
              <select
                value={form.printing_facility}
                onChange={set('printing_facility')}
              >
                <option value="">--</option>
                <option value="dc">Washington, DC</option>
                <option value="fw">Fort Worth, TX</option>
              </select>
            </label>
            <label data-help="series_year">
              Series year
              <input
                type="number"
                value={form.series_year}
                onChange={set('series_year')}
              />
            </label>
            <label data-help="series_letter">
              Series letter
              <input
                type="text"
                maxLength={4}
                value={form.series_letter}
                onChange={set('series_letter')}
              />
            </label>
            {issueWarning.key === factsKey && issueWarning.text && (
              <p className="notice" role="status">
                {issueWarning.text}
              </p>
            )}
            <label data-help="note_type">
              <AccessLabel text="Note class" accessKey="a" />
              <ReferenceSelect
                table="note_type"
                value={form.note_type}
                onChange={set('note_type')}
                {...accel('a')}
              />
              {mark('note_type')}
            </label>
            <label data-help="seal_color">
              Seal color
              <ReferenceSelect
                table="seal_color"
                value={form.seal_color}
                onChange={set('seal_color')}
              />
              {mark('seal_color')}
            </label>
            <label data-help="signature_combination">
              Signatures
              <ReferenceSelect
                table="signature_combination"
                value={form.signature_combination}
                onChange={set('signature_combination')}
              />
              {mark('signature_combination')}
            </label>
            <label data-help="fed_district">
              <AccessLabel text="Reserve Bank" accessKey="b" />
              <ReferenceSelect
                table="fed_district"
                value={form.fed_district}
                onChange={set('fed_district')}
                {...accel('b')}
              />
              {mark('fed_district')}
            </label>
          </>
        )}
      </div>

      {/* Divs, not a wrapping label: the range checkbox needs its own label,
          which a <label> may not contain, so each box is named through
          htmlFor instead -- the same layout the item editor uses.

          Not on a note: its year is its Series year, and a second year box
          is where a series year got typed by mistake (owner, 2026-09-24). */}
      {!isCurrency && (
        <>
          <div data-help="year_start">
            <label htmlFor={yearId}>
              <AccessLabel text={ranged ? 'Year from' : 'Year'} accessKey="y" />
            </label>
            <span className="year-input">
              <input
                id={yearId}
                type="number"
                value={form.year_start}
                onChange={ranged ? set('year_start') : setYear}
                {...accel('y')}
              />
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={ranged}
                  onChange={toggleRange}
                  {...accel('r')}
                />
                {/* */}
                <AccessLabel text="Range of years" accessKey="r" />
              </label>
            </span>
          </div>
          {ranged && (
            <div data-help="year_end">
              <label htmlFor={yearEndId}>
                <AccessLabel text="Year to" accessKey="o" />
              </label>
              <input
                id={yearEndId}
                type="number"
                value={form.year_end}
                onChange={set('year_end')}
                {...accel('o')}
              />
            </div>
          )}
        </>
      )}

      <fieldset data-help="new_item_status">
        <legend>Status</legend>
        <label className="checkbox">
          <input
            type="radio"
            name="new-item-status"
            value="ordered"
            checked={form.status === 'ordered'}
            onChange={set('status')}
          />
          {/* */}
          Ordered
        </label>
        <label className="checkbox">
          <input
            type="radio"
            name="new-item-status"
            value="received"
            checked={form.status === 'received'}
            onChange={set('status')}
          />
          {/* */}
          Received
        </label>
      </fieldset>

      <label data-help="description">
        Description{/* */}
        <textarea rows={3} value={form.description} onChange={set('description')} />
      </label>
      {/* Outside the label, as in the item editor: a button inside a label
          takes the label from its input. */}
      <div className="row">
        <button
          type="button"
          className="link"
          data-help="suggest_description"
          onClick={suggestDescription}
        >
          Suggest description
        </button>
        <span className="muted">{describeNote}</span>
      </div>

      {/* The item does not exist yet, so this is fully controlled: no load,
          no PUT of its own -- `submit` sends the whole set once, right after
          `createInventoryItem` returns an id to send it against. */}
      <ErrorsPanel
        itemId={null}
        kind={form.item_kind}
        value={errors}
        onChange={setErrors}
      />

      <div className="row">
        <button
          type="button"
          disabled={disabled}
          onClick={() => submit(false)}
          {...accel('v')}
        >
          <AccessLabel text={saving ? 'Saving...' : 'Save'} accessKey="v" />
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => submit(true)}
          {...accel('n')}
        >
          <AccessLabel text="Save and add another" accessKey="n" />
        </button>
      </div>
    </div>
  )
}
