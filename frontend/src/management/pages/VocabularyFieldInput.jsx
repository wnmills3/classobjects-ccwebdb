import { ReferenceSelect } from '../../shared/reference'

/**
 * The control for one of a vocabulary's own columns, by the kind the server
 * says it is: a picker over another vocabulary, a choice, a switch, a
 * number or text.
 */
export default function VocabularyFieldInput({ field, value, onChange }) {
  const label = field.label
  if (field.kind === 'reference') {
    return (
      <ReferenceSelect
        table={field.table}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        // One value is being described here; what it points at is picked.
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
  // wheel, and scrolling the page would change what is about to be saved.
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
