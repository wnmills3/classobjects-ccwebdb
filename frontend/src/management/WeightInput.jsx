import { useState } from 'react'

import { WEIGHT_UNITS, fromOzt, toOzt } from './weights'

/**
 * A weight box with its unit beside it: troy ounces or grams.
 *
 * `value` is the stored weight in troy ounces and `onChange` is given troy
 * ounces back (or null for an emptied box), whichever unit is showing: the
 * unit is how the number is typed and read, never what is saved
 * (`weights.js`). Changing the unit re-shows the same weight in the other
 * unit and changes nothing.
 *
 * While the box has focus it shows exactly what was typed, so "0." or a
 * number still being written is not rewritten under the cursor; once focus
 * leaves, it shows the stored weight in the chosen unit.
 *
 * `id` joins the box to its label (`htmlFor`), and `name` is what the unit
 * picker is called for a screen reader: "Gross weight unit".
 */
export default function WeightInput({ id, name, value, onChange }) {
  const [unit, setUnit] = useState('ozt')
  const [typing, setTyping] = useState(null)
  return (
    <span className="weight-input">
      <input
        id={id}
        type="text"
        inputMode="decimal"
        value={typing ?? fromOzt(value, unit)}
        onFocus={() => setTyping(fromOzt(value, unit))}
        onBlur={() => setTyping(null)}
        onChange={(e) => {
          setTyping(e.target.value)
          onChange(toOzt(e.target.value, unit))
        }}
      />
      <select
        aria-label={`${name} unit`}
        value={unit}
        onChange={(e) => setUnit(e.target.value)}
      >
        {WEIGHT_UNITS.map(([code, label]) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
    </span>
  )
}
