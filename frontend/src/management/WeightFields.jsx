import { useId } from 'react'

import WeightInput from './WeightInput'

//: The four weight fields, in the order they are entered: key, label.
const FIELDS = [
  ['gross_weight_ozt', 'Gross weight'],
  ['fineness', 'Fineness'],
  ['fine_weight_ozt', 'Fine weight'],
  ['weight_note', 'Weight as written'],
]

/**
 * What a piece weighs and how much of that is the metal: gross weight,
 * fineness, fine weight, and the weight as it is written.
 *
 * The item editor's weight fields. Each weight is per piece, typed in troy
 * ounces or grams
 * (`WeightInput`) and held in troy ounces. Fine weight may be left empty:
 * the server works it out as gross weight times fineness when it has both.
 *
 * `get(key)` is the field's current value and `set(key, value)` takes the new
 * one -- null for an emptied box, so a save clears the field rather than
 * sending "". `className` is the row's class in the form it sits in, and
 * `aside(key)` renders whatever that form shows beside a field (a
 * "suggested" mark).
 */
export default function WeightFields({ get, set, className, aside }) {
  const base = useId()
  return FIELDS.map(([key, label]) => {
    const id = `${base}-${key}`
    const text = (e) => set(key, e.target.value === '' ? null : e.target.value)
    return (
      <div key={key} className={className} data-help={key}>
        <label htmlFor={id}>{label}</label>
        {key === 'fineness' && (
          <input
            id={id}
            type="text"
            inputMode="decimal"
            placeholder="0.999"
            value={get(key) ?? ''}
            onChange={text}
          />
        )}
        {key === 'weight_note' && (
          <input id={id} type="text" value={get(key) ?? ''} onChange={text} />
        )}
        {key.endsWith('_ozt') && (
          <WeightInput
            id={id}
            name={label}
            value={get(key)}
            onChange={(ozt) => set(key, ozt)}
          />
        )}
        {aside?.(key)}
      </div>
    )
  })
}
