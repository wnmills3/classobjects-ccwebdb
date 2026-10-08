/**
 * The list of purchases, as the console filters and orders it: the picker
 * on the Purchases page and the Order lookup page read one rule each for
 * what a typed filter matches and for how a column sorts.
 */
import { namesPurchase } from './purchase-number'

/**
 * Whether `order` matches a filter typed against part of its order number,
 * its vendor or its seller, or -- exactly -- its own purchase number (`3974`
 * or `#3974`). Capitals aside; an empty filter matches every purchase.
 */
export function matchesFilter(order, filterText) {
  const q = filterText.trim().toLowerCase()
  if (!q) return true
  return (
    namesPurchase(q, order.id) ||
    (order.order_number ?? '').toLowerCase().includes(q) ||
    order.vendor.toLowerCase().includes(q) ||
    (order.seller ?? '').toLowerCase().includes(q)
  )
}

//: Order numbers and vendors compared as people read them: B-9 before B-10,
//: and eBay beside ebay rather than after every capital.
const byText = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Newest first: by date, an undated purchase last, then the latest entered. */
function newest(a, b) {
  if (a.ordered_on !== b.ordered_on) {
    if (!a.ordered_on || !b.ordered_on) return a.ordered_on ? -1 : 1
    return b.ordered_on.localeCompare(a.ordered_on)
  }
  return b.id - a.id
}

//: The columns that hold a count or an amount, compared as numbers: as text,
//: a cost of 9.00 would sort above one of 1,250.00, and a count of nothing
//: would read as a column with nothing in it.
const NUMERIC = new Set(['total', 'outstanding', 'total_cost'])

/**
 * The purchases in the order asked for. A purchase with nothing in the sorted
 * column goes last whichever way it runs -- an undated purchase is not the
 * oldest or the newest -- and ties, one vendor's purchases say, list newest
 * first.
 */
export function sortPurchases(orders, { key, desc }) {
  return [...orders].sort((a, b) => {
    if (NUMERIC.has(key)) {
      const order = Number(a[key]) - Number(b[key])
      return (desc ? -order : order) || newest(a, b)
    }
    const x = a[key] || null
    const y = b[key] || null
    if (x === null || y === null) {
      if (x === y) return newest(a, b)
      return x === null ? 1 : -1
    }
    // ISO dates sort as text; the collator reads the rest.
    const order = key === 'ordered_on' ? x.localeCompare(y) : byText.compare(x, y)
    return (desc ? -order : order) || newest(a, b)
  })
}
