/**
 * Field-by-field merging for the item editor.
 *
 * Two people can have one item open. The editor keeps `base`: the item as it
 * was when each field now being edited was last known to the form. A change
 * made elsewhere then matters only if it touched a field being edited here;
 * every other field simply takes the new value. The server applies the same
 * rule to a save that sends its base (`PATCH /api/inventory/{id}`, `base`),
 * and these comparisons mirror `same_value` in
 * `backend/app/field_changes.py`: were the form to judge two values the same
 * where the server does not, the server would refuse the save over a
 * conflict the form never shows.
 */

import { FIELD_HELP } from '../../fieldHelp'

//: The fields whose values are numbers, and so compare as numbers. Every
//: other field is text: an identifier `0123` is not `123`. Kept equal to
//: `NUMERIC_FIELDS` in `backend/app/field_changes.py`.
export const NUMERIC_FIELDS = new Set([
  'item_cost',
  'shipping_cost',
  'tax_rate',
  'numismatic_value',
  'fineness',
  'gross_weight_ozt',
  'fine_weight_ozt',
  'year_start',
  'year_end',
  'series_year',
  'piece_count',
  'storage_location_id',
])

/** A field's name as a person reads it: its help title, else its key. */
export const fieldName = (key) => FIELD_HELP[key]?.title ?? key.replaceAll('_', ' ')

/** A field's value as the conflict list and the history show it. */
export function shown(value) {
  if (value === null || value === undefined || value === '') return '(blank)'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '(none)'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

/** A field's value on an item as the editor holds it; attributes as codes. */
export function fieldValue(item, key) {
  if (!item) return undefined
  if (key === 'attributes') return (item.attributes ?? []).map((a) => a.code)
  return item[key]
}

/**
 * Whether two values say the same thing: blank and null alike, lists as
 * sets, and with `numeric` -- a field in `NUMERIC_FIELDS` -- numbers as
 * numbers ("84" and "84.00"). Anything else compares as text.
 */
export function sameValue(a, b, { numeric = false } = {}) {
  const blank = (v) => v === null || v === undefined || v === ''
  if (blank(a) && blank(b)) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    // Ordered by code unit, stated rather than left to the default: all that
    // matters is that both lists are put in the same order.
    const byCodeUnit = (x, y) => {
      if (x < y) return -1
      return x > y ? 1 : 0
    }
    const norm = (v) => [...v].map(String).sort(byCodeUnit).join('\u0000')
    return norm(a) === norm(b)
  }
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b
  const isNumber = (v) =>
    typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim()))
  if (numeric && isNumber(a) && isNumber(b)) return Number(a) === Number(b)
  if (blank(a) || blank(b)) return false
  return String(a) === String(b)
}

/** Whether two values of the field `key` say the same thing (`sameValue`). */
const sameIn = (key, a, b) => sameValue(a, b, { numeric: NUMERIC_FIELDS.has(key) })

/** The base a save sends: each edited field's value where the edit began. */
export function baseFor(baseItem, draft) {
  return Object.fromEntries(
    Object.keys(draft).map((key) => [key, fieldValue(baseItem, key)]),
  )
}

/**
 * The edited fields someone else has changed since: stored now differs from
 * where this edit began, and from what this edit would save.
 */
export function conflictsOf(item, baseItem, draft) {
  return Object.keys(draft).filter(
    (key) =>
      !sameIn(key, fieldValue(item, key), fieldValue(baseItem, key)) &&
      !sameIn(key, fieldValue(item, key), draft[key]),
  )
}

/**
 * The new base after reading the item again: the fresh item, except that a
 * field being edited keeps the base its edit began from -- that is what lets
 * a change made elsewhere to that field be noticed rather than adopted.
 */
export function rebase(fresh, previousBase, draft) {
  const next = { ...fresh }
  for (const key of Object.keys(draft)) {
    next[key] = previousBase?.[key]
  }
  return next
}
