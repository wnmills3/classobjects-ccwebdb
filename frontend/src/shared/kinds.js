/**
 * The one place the coin/note split is written down.
 *
 * Two markers record it, both already on the rows and both arriving in a
 * reference value's `extra`: `applies_to` (coin | currency | any) on series,
 * error types and attributes, and `kind` (coin | note) on denominations,
 * where the same face value exists as both and they are different objects.
 *
 * It lives in `shared/` because the shop's catalog filters face the same
 * question, and because a per-form copy is exactly how the item editor came
 * to offer a banknote a metal after the entry form had stopped.
 */

/** Whether an item of this kind is paper money. */
export function isCurrencyKind(kind) {
  return kind === 'currency'
}

/**
 * Which side of the split an item of `itemKind` is on: 'currency' | 'coin'.
 *
 * This is the exact inverse of `fitsKind`'s test, and that is the whole point
 * of its existing: it is what a value ADDED from a picker is marked with, so
 * writing the two rules separately is how an added value comes to fit no kind
 * and vanish from the picker that created it the moment it appears.
 */
export function sideFor(itemKind) {
  return isCurrencyKind(itemKind) ? 'currency' : 'coin'
}

//: Fields a banknote does not have. Coin, bullion, set, medal and token all do.
//: The years among them: a note's year is its series year, and it holds no
//: other (owner, 2026-09-25) -- the server refuses one sent for a note.
export const COIN_ONLY_FIELDS = new Set([
  'strike_type',
  'metal',
  'mint',
  'bullion_form',
  'set_form',
  'variety',
  'year_start',
  'year_end',
])

//: Fields only paper money has.
export const CURRENCY_ONLY_FIELDS = new Set([
  'face_plate_number',
  'back_plate_number',
  'printing_facility',
  'note_type',
  'seal_color',
  'fed_district',
  'signature_combination',
])

/**
 * Whether a FIELD belongs on the form for an item of `itemKind`.
 *
 * Every form asks this question, and asking it here rather than by hand is
 * the point: the drift this module exists to stop -- the item editor keeping
 * a metal box the entry form had already dropped -- began as two forms each
 * holding their own idea of which fields are a coin's.
 *
 * A field named in neither set belongs to both kinds, so the default is true;
 * a form block that holds more than one field is gated on the field that
 * decides it (the entry form's Mint/Variety pair on `mint`).
 */
export function fieldFitsKind(field, itemKind) {
  if (COIN_ONLY_FIELDS.has(field)) return !isCurrencyKind(itemKind)
  if (CURRENCY_ONLY_FIELDS.has(field)) return isCurrencyKind(itemKind)
  return true
}

/** Whether a reference value may be offered for an item of `itemKind`. */
export function fitsKind(entry, itemKind) {
  const side = sideFor(itemKind)
  const appliesTo = entry?.extra?.applies_to
  if (appliesTo) {
    return appliesTo === 'any' || appliesTo === side
  }
  const denominationKind = entry?.extra?.kind
  if (denominationKind) {
    return denominationKind === (side === 'currency' ? 'note' : 'coin')
  }
  return true
}
