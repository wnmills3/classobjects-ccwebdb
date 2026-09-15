/**
 * Money in whole cents, for the order editor's running total.
 *
 * Money crosses the API as decimal strings. Adding them as floats gives
 * 59.97000000000001 for three at 19.99, so the editor works in integer cents
 * and only formats at the edge. The server's total is still the real one.
 */
const MONEY = /^\d+(\.\d{1,2})?$/

export const isMoney = (text) => MONEY.test(String(text).trim())

export function toCents(text) {
  const [whole, fraction = ''] = String(text).trim().split('.')
  return Number(whole) * 100 + Number(`${fraction}00`.slice(0, 2))
}

export function fromCents(cents) {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

export function totalCents(lines) {
  return lines.reduce((sum, line) => {
    const quantity = String(line.quantity)
    if (!/^\d+$/.test(quantity) || !isMoney(line.unit_price)) return sum
    return sum + Number(quantity) * toCents(line.unit_price)
  }, 0)
}
