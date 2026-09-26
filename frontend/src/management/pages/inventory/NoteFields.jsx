import { PRINTING_FACILITIES } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { AccessLabel } from '../../AccessLabel'
import { accel } from '../../shortcuts'

//: A note's own classifiers. Letters are scarce by here: only these two
//: labels hold one that is still free.
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
  // `E82`, `153`, or `FW E82` for a Fort Worth note; the server stores it
  // one way and reads the printing location from it (app.plates).
  ['Face plate', 'face_plate_number', 'text'],
  ['Back plate', 'back_plate_number', 'text'],
]

/**
 * The item editor's rows for a banknote's own fields.
 *
 * `value(key)` is a field's value as the form shows it; `set(key)` is a text
 * box's change handler, `setNumber(key)` a number box's, and
 * `setField(key, value)` sets one outright. `side(key, column)` is the
 * editor's third grid cell: what the lot claimed, and whether the facts
 * filled the value in.
 */
export default function NoteFields({ value, set, setNumber, setField, side }) {
  return (
    <>
      {NOTE_CLASSIFIERS.map(([label, key, table, letter]) => (
        <label key={key} className="field" data-help={key}>
          <AccessLabel text={label} accessKey={letter} />
          <ReferenceSelect
            table={table}
            value={value(key)}
            onChange={set(key)}
            allowAdd={false}
            {...accel(letter)}
          />
          {side(key, `${key}_id`)}
          <span />
        </label>
      ))}
      {NOTE_TEXT_FIELDS.map(([label, key, type]) => (
        <label key={key} className="field" data-help={key}>
          <span>{label}</span>
          <input
            type={type}
            value={value(key)}
            onChange={type === 'number' ? setNumber(key) : set(key)}
          />
          {side(key, key)}
          <span />
        </label>
      ))}
      {/* Read from the face plate when there is one -- FW before it is
          Fort Worth -- so set here only for a note with none. */}
      <label className="field" data-help="printing_facility">
        <span>Printed at</span>
        <select
          value={value('printing_facility') || ''}
          onChange={(e) => setField('printing_facility', e.target.value || null)}
        >
          <option value="">--</option>
          {PRINTING_FACILITIES.map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
        {side('printing_facility', 'printing_facility')}
        <span />
      </label>
    </>
  )
}
