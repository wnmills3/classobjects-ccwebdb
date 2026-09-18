/**
 * The one place the coin/note split is written down.
 *
 * Two markers record it, both already on the rows and both arriving in a
 * reference value's `extra`: `applies_to` (coin | currency | any) on series,
 * error types and attributes, and `kind` (coin | note) on denominations,
 * where the same face value exists as both and they are different objects.
 *
 * It lives in `shared/` because the shop's catalogue filters face the same
 * question, and because a per-form copy is exactly how the item editor came
 * to offer a banknote a metal after the entry form had stopped.
 */

/** Whether an item of this kind is paper money. */
export function isCurrencyKind(kind) {
  return kind === 'currency'
}

//: Fields a banknote does not have. Coin, bullion, set, medal and token all do.
export const COIN_ONLY_FIELDS = new Set([
  'strike_type',
  'metal',
  'mint',
  'bullion_form',
])

//: Fields only paper money has.
export const CURRENCY_ONLY_FIELDS = new Set([
  'note_type',
  'seal_color',
  'fed_district',
  'signature_combination',
])

/** Whether a reference value may be offered for an item of `itemKind`. */
export function fitsKind(entry, itemKind) {
  const currency = isCurrencyKind(itemKind)
  const appliesTo = entry?.extra?.applies_to
  if (appliesTo) {
    return appliesTo === 'any' || appliesTo === (currency ? 'currency' : 'coin')
  }
  const denominationKind = entry?.extra?.kind
  if (denominationKind) {
    return denominationKind === (currency ? 'note' : 'coin')
  }
  return true
}
