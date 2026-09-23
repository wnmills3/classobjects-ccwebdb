/**
 * Money in whole cents, for any arithmetic on the client.
 *
 * Money crosses the API as decimal strings. Adding them as floats gives
 * 59.97000000000001 for three at 19.99, so anything that sums or multiplies
 * works in integer cents and only formats at the edge. The server's figure
 * is still the real one wherever it exists.
 *
 * Shared, not the console's, because the shop's cart totals money too --
 * and did it in floats until this module moved here.
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

// `cents` is always a non-negative integer here -- prices, quantities and
// totals are never negative on either side of the app.
export function fromCents(cents) {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}
