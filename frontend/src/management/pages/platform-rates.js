/**
 * The selling pages' arithmetic: a platform's rates, and an offer's margin.
 *
 * Split out of `Platforms.jsx` because that module exports the page
 * component; a component module that also exports plain functions breaks
 * Fast Refresh (`react-refresh/only-export-components` -- see
 * `docs/code-quality.md`). Kept here so the pages and their tests can both
 * import the helpers without pulling in React.
 *
 * Every calculation a selling page needs belongs here rather than inline in a
 * component: the numbers are money, they are easy to get subtly wrong, and a
 * helper can be tested against its own table of cases.
 */
import { fromCents, isMoney, toCents } from '../../shared/cents'

/** "13.25" (percent, as typed) -> "0.1325"; blank -> null. */
export function percentToFraction(text) {
  const trimmed = String(text ?? '').trim()
  if (trimmed === '') return null
  const [whole, frac = ''] = trimmed.split('.')
  // Shift the decimal point two places left without floating-point error.
  const digits = (whole.padStart(3, '0') + frac).replace(/^0+(?=\d{3})/, '')
  const cut = digits.length - frac.length - 2
  const result = `${digits.slice(0, cut) || '0'}.${digits.slice(cut)}`
  return result.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') || '0'
}

/** "0.1325" -> "13.25"; null -> "". */
export function fractionToPercent(value) {
  if (value === null || value === undefined || value === '') return ''
  const [whole, frac = ''] = String(value).split('.')
  const padded = frac.padEnd(2, '0')
  const shifted = `${whole}${padded.slice(0, 2)}`.replace(/^0+(?=\d)/, '')
  const rest = padded.slice(2).replace(/0+$/, '')
  return rest ? `${shifted}.${rest}` : shifted
}

//: A platform's rate as it is stored: `Numeric(6, 4)`, a fraction between 0
//: and 1, so `0.1325` is 13.25%. Four decimal places exactly, which is what
//: lets a rate be held as a whole number of ten-thousandths.
const RATE = /^\d+(\.\d{1,4})?$/

/** "0.1325" -> 1325 ten-thousandths. Anything unreadable is no rate at all. */
function rateUnits(rate) {
  const text = String(rate ?? '').trim()
  if (!RATE.test(text)) return 0
  const [whole, frac = ''] = text.split('.')
  return Number(whole) * 10000 + Number(`${frac}0000`.slice(0, 4))
}

/** A money amount as stored, or zero when the platform records none. */
const fixedCents = (amount) => (isMoney(amount) ? toCents(amount) : 0)

/**
 * What the platform takes out of a sale at this price, in whole cents.
 *
 * The two rates are added before the single rounding, rather than each being
 * rounded and the results added: a cent of rounding either way is not worth
 * arguing about, but doing it twice makes the estimate disagree with itself
 * depending on how the platform happens to have recorded the same total.
 */
function feeCents(cents, venue) {
  if (!venue) return 0
  const rate = rateUnits(venue.commission_rate) + rateUnits(venue.processing_rate)
  const fixed = fixedCents(venue.processing_fixed) + fixedCents(venue.listing_fee)
  return Math.round((cents * rate) / 10000) + fixed
}

/** Whole cents as a decimal string, negative ones included. */
function money(cents) {
  return cents < 0 ? `-${fromCents(-cents)}` : fromCents(cents)
}

/**
 * Whether anyone has recorded what this platform charges.
 *
 * A recorded zero counts: "this platform takes no commission" is a fact
 * someone entered. An empty column is not that fact -- it is nobody having
 * looked yet -- and the two must not read the same on screen.
 */
export function hasDefaultFees(venue) {
  if (!venue) return false
  return [
    venue.commission_rate,
    venue.processing_rate,
    venue.processing_fixed,
    venue.listing_fee,
  ].some(
    (value) => value !== null && value !== undefined && String(value).trim() !== '',
  )
}

/**
 * The platform's fees on a price, as a decimal string: `'30.82'`.
 *
 * Blank when the price is not an amount, or when the platform has no recorded
 * fees -- a `'0.00'` there would claim the sale costs nothing.
 */
export function estimatedFees(price, venue) {
  if (!isMoney(price) || !hasDefaultFees(venue)) return ''
  return money(feeCents(toCents(price), venue))
}

/**
 * What a sale at this price leaves after the platform's fees.
 *
 * Can be negative, and says so: a lot priced under the platform's own fixed
 * fee loses money on every sale, which is exactly what this is here to show.
 */
export function netAfterFees(price, venue) {
  if (!isMoney(price) || !hasDefaultFees(venue)) return ''
  const cents = toCents(price)
  return money(cents - feeCents(cents, venue))
}

/**
 * The margin on an offer after the platform's fees, as a percentage to one
 * decimal place: `netMarginPercent('189.00', '120.00', ebay)` is `'20.2'`.
 *
 * Blank -- not `'0'` -- when the margin cannot be known: an item whose cost
 * nobody has recorded yet, or a price of zero. "Sold at cost" and "nobody has
 * costed this" are different facts, and a zero here would report the first
 * when only the second is true. A platform with no recorded fees is charged
 * none, which makes this the gross margin rather than a guess.
 *
 * Whole cents throughout, via `shared/cents.js` and for the reason written
 * there: money crosses the API as a decimal string, and a float cannot hold
 * cents exactly. Only the final division is inexact, and its result is
 * rounded immediately and used for display alone.
 */
export function netMarginPercent(price, costBasis, venue) {
  if (!isMoney(price) || !isMoney(costBasis)) return ''
  const cents = toCents(price)
  if (cents === 0) return ''
  const net = cents - feeCents(cents, venue)
  return String(Math.round(((net - toCents(costBasis)) * 1000) / cents) / 10)
}

/**
 * The gross margin on an offer, as a percentage to one decimal place:
 * `marginPercent('189.00', '120.00')` is `'36.5'`.
 *
 * What a listing that has already been made is worth: no platform's fees are
 * deducted, because a listing row carries the price and the cost and nothing
 * about what the sale will cost to make. `netMarginPercent` is the same
 * calculation with a platform's fees taken out, and this is that with none.
 */
export function marginPercent(price, costBasis) {
  return netMarginPercent(price, costBasis, null)
}
