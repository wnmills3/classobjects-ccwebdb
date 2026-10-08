import { useState } from 'react'

import { api } from '../api'
import VocabularyFieldInput from './VocabularyFieldInput'
import { blank, fieldHelp, filled, sent } from './vocabulary-fields'

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

  const missing = fields.find(
    (field) => field.required && !filled(field, extra[field.name]),
  )
  // What Add is waiting for, said beside it; '' once it can be sent.
  const waiting = !label.trim()
    ? 'A label is needed first.'
    : !/^\d*$/.test(order)
      ? 'Position is a whole number.'
      : missing
        ? `${missing.label} is needed first.`
        : ''
  const ready = waiting === ''

  async function add(e) {
    e.preventDefault()
    if (!ready) return
    setSaving(true)
    try {
      const given = Object.fromEntries(
        fields
          .filter((field) => filled(field, extra[field.name]))
          .map((field) => [field.name, sent(field, extra[field.name])]),
      )
      onAdded(
        await api.addReferenceValue(table, {
          label: label.trim(),
          ...(code.trim() ? { code: code.trim() } : {}),
          ...(order ? { sort_order: Number(order) } : {}),
          extra: given,
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
        <label data-help="vocabulary_label">
          Label{/* */}
          <input
            value={label}
            maxLength={255}
            autoFocus
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        {fields.map((field) => (
          <label key={field.name} data-help={fieldHelp(field.name)}>
            {field.label}
            {field.required ? '' : ' (optional)'}
            <VocabularyFieldInput
              field={field}
              value={extra[field.name]}
              onChange={(next) => setExtra((was) => ({ ...was, [field.name]: next }))}
            />
          </label>
        ))}
        {sequenced && (
          <label data-help="vocabulary_position">
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
        <label data-help="vocabulary_code">
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
        <button type="submit" data-help="vocabulary_add" disabled={!ready || saving}>
          {saving ? 'Adding...' : 'Add value'}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
        {waiting && <span className="muted">{waiting}</span>}
      </div>
    </form>
  )
}
