/**
 * Field-by-field merging for the item editor.
 *
 * Two people can have one item open. The editor keeps `base`: the item as it
 * was when each field now being edited was last known to the form. A change
 * made elsewhere then matters only if it touched a field being edited here;
 * every other field simply takes the new value. The server applies the same
 * rule to a save that sends its base (`PATCH /inventory/{id}`, `base`), and
 * these comparisons mirror `_same_value` there.
 */

/** A field's value on an item as the editor holds it; attributes as codes. */
export function fieldValue(item, key) {
  if (!item) return undefined
  if (key === 'attributes') return (item.attributes ?? []).map((a) => a.code)
  return item[key]
}

/**
 * Whether two values say the same thing: blank and null alike, numbers as
 * numbers ("84" and "84.00"), lists as sets.
 */
export function sameValue(a, b) {
  const blank = (v) => v === null || v === undefined || v === ''
  if (blank(a) && blank(b)) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    const norm = (v) => [...v].map(String).sort().join('\u0000')
    return norm(a) === norm(b)
  }
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b
  const numeric = (v) =>
    typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim()))
  if (numeric(a) && numeric(b)) return Number(a) === Number(b)
  return a === b
}

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
      !sameValue(fieldValue(item, key), fieldValue(baseItem, key)) &&
      !sameValue(fieldValue(item, key), draft[key]),
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
