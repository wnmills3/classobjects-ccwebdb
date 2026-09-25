/**
 * What an item gives up when its kind changes in the editor.
 *
 * A banknote has no metal or strike type; a coin has no seal or serial
 * number. The server refuses a save that would leave either on the wrong kind
 * (a metal on a note, a note's serial on a coin), and a picker that no longer
 * offers the stored value would hide it rather than drop it. So choosing a new
 * kind empties every field that does not fit it, in the draft, where the
 * editor can say what went and put it back if the kind is changed back.
 *
 * Kinds on the same side -- a coin becoming a set -- give up nothing.
 */

import { fieldFitsKind, fitsKind, isCurrencyKind } from '../../../shared/kinds'

//: The editor's pickers that belong to one side only.
const SIDED_CLASSIFIERS = [
  'strike_type',
  'metal',
  'mint',
  'note_type',
  'seal_color',
  'fed_district',
  'signature_combination',
]

//: A note's own fields that are not pickers: gone with the note.
const NOTE_SCALARS = ['series_year', 'series_letter', 'serial_number']

//: What a coin weighs and is made of. Not on the form, but a note that kept
//: them would go on counting as silver in the collection's fine-metal total.
const COIN_MEASURES = ['fineness', 'gross_weight_ozt', 'fine_weight_ozt', 'variety']

//: Pickers whose values each belong to a side, and the table each reads.
const SIDED_VALUES = [
  ['denomination', 'denomination'],
  ['series', 'series'],
  ['grade_designation', 'grade_designation'],
]

const isSet = (value) => value !== null && value !== undefined && value !== ''

/**
 * The fields a change to `kind` empties, and the attributes it drops.
 *
 * `current(key)` is a field's value now -- the draft's, else the item's --
 * with `attributes` as a list of codes. `vocab` holds the loaded reference
 * tables by name. A value whose entry is not loaded is kept: the server then
 * refuses the save by name, which is better than dropping a value unseen.
 *
 * Returns `{fields, attributes}`: `fields` maps each emptied field to the
 * value it held; `attributes` is `{keep, dropped}` when any are dropped,
 * else null.
 */
export function clearedByKind(kind, current, vocab) {
  const fields = {}
  const toNote = isCurrencyKind(kind)

  for (const key of SIDED_CLASSIFIERS) {
    if (!fieldFitsKind(key, kind) && isSet(current(key))) fields[key] = current(key)
  }
  for (const key of toNote ? COIN_MEASURES : NOTE_SCALARS) {
    if (isSet(current(key))) fields[key] = current(key)
  }
  for (const [key, table] of SIDED_VALUES) {
    const code = current(key)
    const entry = vocab[table]?.find((candidate) => candidate.code === code)
    if (isSet(code) && entry && !fitsKind(entry, kind)) fields[key] = code
  }
  // A note is graded on its own scale; every other scale is a coin's.
  const grade = current('grade')
  const graded = vocab.grade?.find((candidate) => candidate.code === grade)
  if (isSet(grade) && graded && (graded.extra?.grade_scale === 'note') !== toNote) {
    fields.grade = grade
  }

  const codes = current('attributes') ?? []
  const byCode = new Map((vocab.item_attribute ?? []).map((a) => [a.code, a]))
  const dropped = codes.filter(
    (code) => byCode.has(code) && !fitsKind(byCode.get(code), kind),
  )
  const attributes = dropped.length
    ? { keep: codes.filter((code) => !dropped.includes(code)), dropped }
    : null

  return { fields, attributes }
}
