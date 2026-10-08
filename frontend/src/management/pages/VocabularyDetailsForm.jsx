import { useState } from 'react'

import { api } from '../api'
import VocabularyFieldInput from './VocabularyFieldInput'
import { fieldHelp, filled, held, sent } from './vocabulary-fields'

/**
 * Change what a vocabulary records about one of its values: which items a
 * series is offered for, a denomination's face value, a mint's mark. The
 * label and the code are not here -- Rename changes the one, and nothing
 * changes the other.
 *
 * Only the fields changed are sent, so a detail someone else has since
 * changed is not put back by saving another. An emptied field is sent
 * empty, which clears it; the server refuses that where the column cannot
 * be empty, and a required field holds Save back before it gets that far.
 */
export default function VocabularyDetailsForm({
  table,
  value,
  fields,
  onSaved,
  onCancel,
}) {
  const [before] = useState(() => held(fields, value.extra))
  const [draft, setDraft] = useState(before)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const changed = fields.filter((field) => draft[field.name] !== before[field.name])
  const missing = fields.find(
    (field) => field.required && !filled(field, draft[field.name]),
  )
  // What Save is waiting for, said beside it; '' once it can be sent.
  const waiting = missing
    ? `${missing.label} is needed.`
    : changed.length === 0
      ? 'Nothing is changed yet.'
      : ''
  const ready = waiting === ''

  async function save(e) {
    e.preventDefault()
    if (!ready) return
    setSaving(true)
    try {
      onSaved(
        // The label always travels: the endpoint renames in the same call.
        await api.renameReferenceValue(table, value.code, {
          label: value.label,
          extra: Object.fromEntries(
            changed.map((field) => [field.name, sent(field, draft[field.name])]),
          ),
        }),
      )
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  return (
    <form
      className="vocabulary-add"
      onSubmit={save}
      aria-label={`Details of ${value.label}`}
    >
      <div className="filter-grid">
        {fields.map((field) => (
          <label key={field.name} data-help={fieldHelp(field.name)}>
            {field.label}
            {field.required ? '' : ' (optional)'}
            <VocabularyFieldInput
              field={field}
              value={draft[field.name]}
              onChange={(next) => setDraft((was) => ({ ...was, [field.name]: next }))}
            />
          </label>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button
          type="submit"
          data-help="vocabulary_details"
          disabled={!ready || saving}
        >
          {saving ? 'Saving...' : 'Save details'}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
        {waiting && <span className="muted">{waiting}</span>}
      </div>
    </form>
  )
}
