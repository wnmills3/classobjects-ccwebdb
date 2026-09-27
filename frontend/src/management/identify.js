import { fieldFitsKind, isCurrencyKind } from '../shared/kinds'

/**
 * The fields that identify a piece of this kind, in the order they are
 * written: "Series 1934-A $5" and its serial and plates for a note, "1921-D
 * $1" for anything else. Entry starts with these, and most other classifiers
 * follow from them (`docs/specs/identify-first-entry-design.md`).
 *
 * Whether the kind has a mint is asked of `fieldFitsKind`, the one list of
 * which fields are a coin's.
 */
export function identifyKeys(kind) {
  return isCurrencyKind(kind)
    ? [
        'series_year',
        'series_letter',
        'denomination',
        'serial_number',
        'face_plate_number',
        'back_plate_number',
      ]
    : ['year_start', ...(fieldFitsKind('mint', kind) ? ['mint'] : []), 'denomination']
}

//: The facts that are numbers; the rest cross as text.
export const NUMBERS = new Set(['series_year', 'year_start'])

/** An item's identifying facts as the section's text, blank for unknown. */
export function identifyValues(item) {
  return Object.fromEntries(
    identifyKeys(item.item_kind).map((key) => [
      key,
      item[key] == null ? '' : String(item[key]),
    ]),
  )
}

/** A typed value as the API takes it: trimmed, a number where one, blank null. */
function sent(key, text) {
  const trimmed = (text ?? '').trim()
  if (trimmed === '') return null
  return NUMBERS.has(key) ? Number(trimmed) : trimmed
}

/**
 * The facts that moved, and what each was, as `PATCH /api/inventory/{id}`
 * takes them: the change and its `base`, so a save merges field by field
 * rather than overwriting whatever else changed meanwhile.
 *
 * A coin's year is one year, so it moves as both ends -- the editor's rule.
 */
export function identifyChanges(item, values) {
  const changes = {}
  const base = {}
  for (const key of identifyKeys(item.item_kind)) {
    const before = item[key] ?? null
    const after = sent(key, values[key])
    if (after === before) continue
    changes[key] = after
    base[key] = before
    if (key === 'year_start') {
      changes.year_end = after
      base.year_end = item.year_end ?? null
    }
  }
  return { changes, base }
}
