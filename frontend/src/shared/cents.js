/**
 * Money in whole cents, for any arithmetic on the client.
 *
 * Money crosses the API as decimal strings. Adding them as floats gives
 * 59.97000000000001 for three at 19.99, so anything that sums or multiplies
 * works in integer cents and only formats at the edge. The server's figure
 * is still the real one wherever it exists.
 *
 * Shared because both applications total money: the shop's cart, and the
 * console's order, sale and settlement figures.
 */
const MONEY = /^\d+(\.\d{1,2})?$/

export const isMoney = (text) => MONEY.test(String(text).trim())

export function toCents(text) {
  if (!isMoney(text)) {
    throw new RangeError(`not a money amount: ${JSON.stringify(text)}`)
  }
  const [whole, fraction = ''] = String(text).trim().split('.')
  return Number(whole) * 100 + Number(`${fraction}00`.slice(0, 2))
}

/**
 * Typed or stored money text in cents, or zero when it is blank or not an
 * amount -- for a running total that counts only what has been filled in.
 */
export function centsOrZero(text) {
  const trimmed = String(text ?? '').trim()
  return isMoney(trimmed) ? toCents(trimmed) : 0
}

/**
 * Non-negative whole cents as a decimal string: 18950 is `'189.50'`.
 *
 * Only for a figure that cannot be negative -- a price, a quantity's total.
 * `Math.floor` and `%` both go wrong below zero, so a net or a margin, which
 * can be, goes through `signedFromCents`.
 */
export function fromCents(cents) {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

/** Whole cents as a decimal string, negative ones included: -250 is `'-2.50'`. */
export function signedFromCents(cents) {
  return cents < 0 ? `-${fromCents(-cents)}` : fromCents(cents)
}
