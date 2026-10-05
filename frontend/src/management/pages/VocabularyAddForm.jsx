import { useState } from 'react'

import { api } from '../api'
import { ReferenceSelect } from '../../shared/reference'

/** What each field starts as: a switch off, everything else empty. */
function blank(fields) {
  return Object.fromEntries(
    fields.map((field) => [field.name, field.kind === 'boolean' ? false : '']),
  )
}

const filled = (field, value) => field.kind === 'boolean' || String(value).trim() !== ''

function FieldInput({ field, value, onChange }) {
  const label = field.label
  if (field.kind === 'reference') {
    return (
      <ReferenceSelect
        table={field.table}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        // One thing is being added here; what it points at is picked.
        allowAdd={false}
      />
    )
  }
  if (field.kind === 'choice') {
    return (
      <select
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">--</option>
        {field.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    )
  }
  if (field.kind === 'boolean') {
    return (
      <input
        type="checkbox"
        checked={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
    )
  }
  // Text with a pattern, not type=number: a number input steps on the mouse
  // wheel, and scrolling the page would change what is about to be added.
  const numeric =
    field.kind === 'integer'
      ? { inputMode: 'numeric', pattern: '-?[0-9]+' }
      : field.kind === 'decimal'
        ? { inputMode: 'decimal', pattern: '-?[0-9]*[.]?[0-9]+' }
        : {}
  return (
    <input
      value={value}
      aria-label={label}
      maxLength={field.max_length ?? undefined}
      onChange={(e) => onChange(e.target.value)}
      {...numeric}
    />
  )
}

/**
 * Add a value to a vocabulary: its label, where it sits when the vocabulary
 * is in a meaningful order, and whatever is the vocabulary's own -- a
 * denomination's currency, face value and side. `fields` is the server's
 * account of those, so a vocabulary that gains a column gains a box.
 *
 * The code is worked out by the server unless one is typed: nobody adding
 * "Three Cents" should have to know it is `usd_coin_0_03`.
 */
export default function VocabularyAddForm({
  table,
  fields,
  sequenced,
  onAdded,
  onCancel,
}) {
  const [label, setLabel] = useState('')
  const [code, setCode] = useState('')
  const [order, setOrder] = useState('')
  const [extra, setExtra] = useState(() => blank(fields))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const ready =
    label.trim() &&
    /^\d*$/.test(order) &&
    fields.every((field) => !field.required || filled(field, extra[field.name]))

  async function add(e) {
    e.preventDefault()
    if (!ready) return
    setSaving(true)
    try {
      const sent = Object.fromEntries(
        fields
          .filter((field) => filled(field, extra[field.name]))
          .map((field) => [
            field.name,
            field.kind === 'boolean' ? extra[field.name] : extra[field.name].trim(),
          ]),
      )
      onAdded(
        await api.addReferenceValue(table, {
          label: label.trim(),
          ...(code.trim() ? { code: code.trim() } : {}),
          ...(order ? { sort_order: Number(order) } : {}),
          extra: sent,
        }),
      )
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  return (
    <form className="vocabulary-add" onSubmit={add} aria-label={`Add to ${table}`}>
      <div className="filter-grid">
        <label>
          Label{/* */}
          <input
            value={label}
            maxLength={255}
            autoFocus
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        {fields.map((field) => (
          <label key={field.name}>
            {field.label}
            {field.required ? '' : ' (optional)'}
            <FieldInput
              field={field}
              value={extra[field.name]}
              onChange={(next) => setExtra((was) => ({ ...was, [field.name]: next }))}
            />
          </label>
        ))}
        {sequenced && (
          <label>
            Position (optional){/* */}
            <input
              inputMode="numeric"
              pattern="[0-9]*"
              value={order}
              placeholder="last"
              title="Where it sits in the list: between the positions shown below"
              onChange={(e) => setOrder(e.target.value)}
            />
          </label>
        )}
        <label>
          Code (optional){/* */}
          <input
            value={code}
            maxLength={64}
            placeholder="worked out for you"
            title="What records and saved searches store. It never changes."
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="submit" disabled={!ready || saving}>
          {saving ? 'Adding...' : 'Add value'}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
