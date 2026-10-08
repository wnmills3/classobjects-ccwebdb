/**
 * Weight as the console's forms take it: stored in troy ounces, typed in troy
 * ounces or grams.
 *
 * The database holds a weight in troy ounces to six places (`gross_weight_ozt`,
 * `fine_weight_ozt`), as a decimal string that never passes through a float
 * on its way to the API when it is typed in ounces. Grams are an entry
 * convenience: what is typed is converted once, to six places, and the
 * ounces are what is saved.
 */

//: Grams in one troy ounce, exactly (the international troy ounce).
export const GRAMS_PER_OZT = 31.1034768

//: The units a weight can be typed in: code, the name in the picker.
export const WEIGHT_UNITS = [
  ['ozt', 'troy oz'],
  ['g', 'grams'],
]

const NUMBER = /^(\d+(\.\d*)?|\.\d+)$/

/**
 * What to save for `text` typed in `unit`: troy ounces as a decimal string.
 *
 * Empty text is null -- the field is cleared. Text that is not a number is
 * handed back as typed, so the save is refused naming the field rather than
 * the entry vanishing.
 */
export function toOzt(text, unit) {
  const typed = String(text ?? '').trim()
  if (typed === '') return null
  if (!NUMBER.test(typed)) return typed
  if (unit === 'ozt') return typed
  return (Number(typed) / GRAMS_PER_OZT).toFixed(6)
}

/**
 * A stored weight in `unit`, as the box shows it: no trailing zeros, grams to
 * three places. '' for no weight.
 */
export function fromOzt(ozt, unit) {
  if (ozt === null || ozt === undefined || ozt === '') return ''
  const stored = Number(ozt)
  if (!Number.isFinite(stored)) return String(ozt)
  if (unit === 'ozt') return String(stored)
  return String(Number((stored * GRAMS_PER_OZT).toFixed(3)))
}
