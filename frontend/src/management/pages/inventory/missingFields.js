/**
 * The fields the `missing=<field>` filter can look for, as a person reads them.
 *
 * The keys are exactly the backend's `inventory_search.MISSING_FIELDS` keys
 * -- the `missing=` values, and the completeness report's percent-column
 * keys -- so a drill from a report lands on a filter this page can name.
 */
export const MISSING_FIELDS = {
  year: 'year',
  denomination: 'denomination',
  grade: 'grade',
  country: 'country',
  series: 'series',
  metal: 'metal',
  photo: 'photograph',
  storage_location: 'storage location',
  listing_link: 'listing link',
  sellers_item_id: "seller's item id",
}

/**
 * An active `missing=` filter in words: "Missing: photograph". A field this
 * list does not know is still shown, by its own name, rather than hidden --
 * an invisible filter is the problem this label exists to solve.
 */
export function missingLabel(field) {
  return `Missing: ${MISSING_FIELDS[field] ?? String(field).replaceAll('_', ' ')}`
}
