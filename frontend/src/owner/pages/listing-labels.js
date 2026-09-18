/**
 * What the selling screens call a listing's format and status.
 *
 * A plain module rather than exports from `Listings.jsx`: a component module
 * that also exports something else breaks Fast Refresh
 * (`react-refresh/only-export-components` -- see `docs/code-quality.md`), and
 * the Listings page, the item editor's offers panel and the offer dialog all
 * name the same codes. One table so they cannot drift apart.
 */

/** The listing formats the API accepts (`ListingFormat`), with their labels. */
export const FORMATS = [
  ['fixed_price', 'Fixed price'],
  ['auction', 'Auction'],
]

/** The listing statuses the API reports (`ListingStatus`), with their labels. */
export const STATUSES = [
  ['active', 'Active'],
  ['paused', 'Paused'],
  ['ended', 'Ended'],
]

//: The two statuses that mean an item is spoken for -- the pair
//: `offering_writes.ON_OFFER` names, so the console and the writer agree
//: about what "offered" means.
export const ON_OFFER = ['active', 'paused']

/**
 * The label for a code, or the code itself when this console has not been
 * taught about it.
 *
 * Never the first entry in the table: a value the console does not know must
 * be visible as itself rather than silently renamed to some default.
 */
export const labelFor = (table, code) =>
  table.find(([value]) => value === code)?.[1] ?? code

/** Nothing to show, in a place a cell would otherwise be blank. */
export const UNKNOWN = '--'
