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
import { isMoney, toCents } from './orders/cents'

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

/**
 * The gross margin on an offer, as a percentage to one decimal place:
 * `marginPercent('189.00', '120.00')` is `'36.5'`.
 *
 * Blank -- not `'0'` -- when the margin cannot be known: an item whose cost
 * nobody has recorded yet, or a price of zero. "Sold at cost" and "nobody has
 * costed this" are different facts, and a zero here would report the first
 * when only the second is true.
 *
 * Whole cents throughout, via `orders/cents.js` and for the reason written
 * there: money crosses the API as a decimal string, and a float cannot hold
 * cents exactly. Only the final division is inexact, and its result is
 * rounded immediately and used for display alone.
 */
export function marginPercent(price, costBasis) {
  if (!isMoney(price) || !isMoney(costBasis)) return ''
  const cents = toCents(price)
  if (cents === 0) return ''
  return String(Math.round(((cents - toCents(costBasis)) * 1000) / cents) / 10)
}
