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

/**
 * Whether a listing offers a lot rather than a single item.
 *
 * `sales_lot_id`, not "item_code is missing": the two are exclusive by
 * `ck_listing_item_xor_lot`, and asking the positive question means a row
 * that arrives with neither -- an older response, a shape nobody planned --
 * reads as an item rather than being announced as a group of coins it is
 * not.
 */
export function isLot(listing) {
  return listing.sales_lot_id !== null && listing.sales_lot_id !== undefined
}

/**
 * What a listing is an offer *of*, named the way a person would name it.
 *
 * An item's permanent code for an item listing, and for a **lot** listing the
 * lot's own title with how many coins are in it. `ListingOut.item_code` has
 * been nullable since lots existed -- a lot is not an item and has no code --
 * and every screen that read it unconditionally printed the word `null`:
 * "Edit null on eBay", "null is withdrawn from eBay at 1000.00", and a blank
 * first cell on the page whose whole job is listing offers.
 *
 * Both halves come off the row itself. `item_title` is the item's
 * `source_title` or the **lot's** title -- one field the console can always
 * show, whichever kind the row is -- and `member_count` is how many coins the
 * lot holds, sent so this needs no request per row. The count is included
 * because it is the difference that matters: "Three Morgans" priced at
 * 1000.00 reads as one coin without it.
 *
 * A row with neither a code nor a count -- an older response, or a lot whose
 * count the API could not give -- falls back to the title alone rather than
 * inventing a number.
 */
export function subjectOf(listing) {
  if (listing.item_code) return listing.item_code
  const count = listing.member_count
  if (count === null || count === undefined) return listing.item_title
  return `${listing.item_title} (${count} ${count === 1 ? 'item' : 'items'})`
}
