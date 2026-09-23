/**
 * Money in whole cents, for the order editor's running total.
 *
 * The conversions live in `shared/cents.js`, which the shop's cart uses too;
 * they are re-exported here so the console's callers are unchanged.
 */
import { isMoney, toCents } from '../../../shared/cents'

export { fromCents, isMoney, toCents } from '../../../shared/cents'

export function totalCents(lines) {
  return lines.reduce((sum, line) => {
    const quantity = String(line.quantity)
    if (!/^\d+$/.test(quantity) || !isMoney(line.unit_price)) return sum
    return sum + Number(quantity) * toCents(line.unit_price)
  }, 0)
}
