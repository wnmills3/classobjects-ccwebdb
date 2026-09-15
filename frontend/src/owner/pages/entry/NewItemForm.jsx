import { useId, useRef, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'
import { AccessLabel } from '../../AccessLabel'
import { accel, useSaveShortcut } from '../../shortcuts'
import { isMoney } from '../orders/cents'

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
  'status',
  'country',
  'denomination',
  'series',
  'series_year',
  'series_letter',
  'seal_color',
  'fed_district',
  'note_type',
  'grading_service',
  'metal',
  'mint',
]

const BLANK = {
  item_kind: 'coin',
  source_title: '',
  description: '',
  year_start: '',
  year_end: '',
  piece_count: '1',
  item_cost: '',
  shipping_cost: '',
  status: 'ordered',
  country: '',
  denomination: '',
  grade: '',
  grade_designation: '',
  grading_service: '',
  cert_number: '',
  metal: '',
  series: '',
  mint: '',
  variety: '',
  serial_number: '',
  series_year: '',
  series_letter: '',
  seal_color: '',
  fed_district: '',
  note_type: '',
}

/** An emptied number box clears the year rather than sending "". */
const yearValue = (text) => (text === '' ? '' : text)

/** Whether `kind` is graded and detailed as a banknote rather than a coin. */
const isCurrencyKind = (kind) => kind === 'currency'

export default function NewItemForm({
  purchaseOrderId,
  defaults,
  onSaved,
  disabledReason = '',
}) {
  const [form, setForm] = useState(BLANK)
  const [ranged, setRanged] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const titleRef = useRef(null)
  const yearId = useId()
  const yearEndId = useId()

  const isCurrency = isCurrencyKind(form.item_kind)

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

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
    setForm((f) => ({
      ...f,
      item_kind: kind,
      grade: isCurrencyKind(kind) === isCurrencyKind(f.item_kind) ? f.grade : '',
    }))
  }

  /** The `ItemCreate` body, or a thrown `Error` naming the first bad field. */
  function buildPayload() {
    if (!form.source_title.trim()) {
      throw new Error('Title is required.')
    }
    if (ranged && form.year_start === '' && form.year_end !== '') {
      throw new Error('Enter Year from, or clear Year to.')
    }
    const payload = {
      purchase_order_id: purchaseOrderId,
      item_kind: form.item_kind,
      source_title: form.source_title.trim(),
      description: form.description,
      piece_count: form.piece_count === '' ? 1 : Number(form.piece_count),
      status: form.status,
      // The purchase's tax defaults, resolved once for every item entered on
      // it -- not something this form asks about item by item.
      tax_rate: defaults?.tax_rate ?? null,
      tax_includes_shipping: defaults?.tax_includes_shipping ?? null,
    }

    if (form.year_start !== '') {
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
      'grade',
      'grade_designation',
      'grading_service',
      'series',
    ]) {
      if (form[key]) payload[key] = form[key]
    }
    if (form.cert_number) payload.cert_number = form.cert_number

    // Coin and currency detail never cross: sending both is a 422 naming the
    // field, so only the block that matches the kind is ever included.
    if (isCurrency) {
      if (form.serial_number) payload.serial_number = form.serial_number
      if (form.series_year !== '') payload.series_year = Number(form.series_year)
      if (form.series_letter) payload.series_letter = form.series_letter
      if (form.seal_color) payload.seal_color = form.seal_color
      if (form.fed_district) payload.fed_district = form.fed_district
      if (form.note_type) payload.note_type = form.note_type
    } else {
      if (form.metal) payload.metal = form.metal
      if (form.mint) payload.mint = form.mint
      if (form.variety) payload.variety = form.variety
    }

    return payload
  }

  async function submit(addAnother) {
    setSaving(true)
    try {
      const payload = buildPayload()
      const created = await api.createInventoryItem(payload)
      setError('')
      if (addAnother) {
        const kept = Object.fromEntries(SHARED_ON_REPEAT.map((k) => [k, form[k]]))
        setForm({ ...BLANK, ...kept })
        setRanged(false)
        titleRef.current?.focus()
      } else {
        setForm(BLANK)
        setRanged(false)
      }
      onSaved?.(created)
    } catch (err) {
      // Kept in place: a refusal here (a 422 naming a field, an unknown
      // purchase order) must not throw away what was typed.
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const disabled = saving || Boolean(disabledReason)

  useSaveShortcut(() => submit(false), !disabled && form.source_title.trim() !== '')

  return (
    <div className="admin-form">
      <h3>New item</h3>
      {error && <p className="error">{error}</p>}
      {disabledReason && <p className="error">{disabledReason}</p>}

      <div className="form-grid">
        <label>
          <AccessLabel text="Kind" accessKey="k" />
          <ReferenceSelect
            table="item_kind"
            value={form.item_kind}
            onChange={setKind}
            allowBlank={false}
            {...accel('k')}
          />
        </label>

        <label>
          <AccessLabel text="Title" accessKey="t" />
          <input
            ref={titleRef}
            type="text"
            value={form.source_title}
            onChange={set('source_title')}
            {...accel('t')}
          />
        </label>

        <label>
          <AccessLabel text="Pieces" accessKey="p" />
          <input
            type="number"
            min="1"
            value={form.piece_count}
            onChange={set('piece_count')}
            {...accel('p')}
          />
        </label>

        <label>
          <AccessLabel text="Item cost" accessKey="i" />
          <input
            type="text"
            inputMode="decimal"
            value={form.item_cost}
            onChange={set('item_cost')}
            {...accel('i')}
          />
        </label>

        <label>
          <AccessLabel text="Shipping" accessKey="h" />
          <input
            type="text"
            inputMode="decimal"
            value={form.shipping_cost}
            onChange={set('shipping_cost')}
            {...accel('h')}
          />
        </label>

        <label>
          <AccessLabel text="Country" accessKey="u" />
          <ReferenceSelect
            table="country"
            value={form.country}
            onChange={set('country')}
            placeholder="US"
            {...accel('u')}
          />
        </label>

        <label>
          <AccessLabel text="Denomination" accessKey="m" />
          <ReferenceSelect
            table="denomination"
            value={form.denomination}
            onChange={set('denomination')}
            {...accel('m')}
          />
        </label>

        <label>
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

        <label>
          Grade designation
          <ReferenceSelect
            table="grade_designation"
            value={form.grade_designation}
            onChange={set('grade_designation')}
          />
        </label>

        <label>
          Grading service
          <ReferenceSelect
            table="grading_service"
            value={form.grading_service}
            onChange={set('grading_service')}
          />
        </label>

        <label>
          Certificate number
          <input type="text" value={form.cert_number} onChange={set('cert_number')} />
        </label>

        {!isCurrency && (
          <label>
            <AccessLabel text="Metal" accessKey="l" />
            <ReferenceSelect
              table="metal"
              value={form.metal}
              onChange={set('metal')}
              {...accel('l')}
            />
          </label>
        )}

        <label>
          <AccessLabel text="Series" accessKey="s" />
          <ReferenceSelect
            table="series"
            value={form.series}
            onChange={set('series')}
            {...accel('s')}
          />
        </label>

        {!isCurrency && (
          <>
            <label>
              Mint
              <ReferenceSelect table="mint" value={form.mint} onChange={set('mint')} />
            </label>
            <label>
              Variety
              <input type="text" value={form.variety} onChange={set('variety')} />
            </label>
          </>
        )}

        {isCurrency && (
          <>
            <label>
              Serial number
              <input
                type="text"
                value={form.serial_number}
                onChange={set('serial_number')}
              />
            </label>
            <label>
              Series year
              <input
                type="number"
                value={form.series_year}
                onChange={set('series_year')}
              />
            </label>
            <label>
              Series letter
              <input
                type="text"
                maxLength={4}
                value={form.series_letter}
                onChange={set('series_letter')}
              />
            </label>
            <label>
              Seal colour
              <ReferenceSelect
                table="seal_color"
                value={form.seal_color}
                onChange={set('seal_color')}
              />
            </label>
            <label>
              Federal Reserve district
              <ReferenceSelect
                table="fed_district"
                value={form.fed_district}
                onChange={set('fed_district')}
              />
            </label>
            <label>
              Note type
              <ReferenceSelect
                table="note_type"
                value={form.note_type}
                onChange={set('note_type')}
              />
            </label>
          </>
        )}
      </div>

      {/* Divs, not a wrapping label: the range checkbox needs its own label,
          which a <label> may not contain, so each box is named through
          htmlFor instead -- the same layout the item editor uses. */}
      <div>
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
        <div>
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

      <fieldset>
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

      <label>
        Description{/* */}
        <textarea rows={3} value={form.description} onChange={set('description')} />
      </label>

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
