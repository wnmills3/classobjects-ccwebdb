/**
 * What the settlement grid calls a lot's result.
 *
 * A plain module rather than exports from `SettlementGrid.jsx`, for the
 * same reason `listing-labels.js` is: a component module that also exports
 * something else breaks Fast Refresh (`react-refresh/only-export-components`
 * -- see `docs/code-quality.md`), and both `SettlementGrid.jsx` and
 * `Auctions.jsx` need to agree on the same codes and labels.
 */

/** `AuctionLotResult`, with the labels the grid reads. */
export const RESULTS = [
  ['sold', 'Sold'],
  ['unsold', 'Unsold'],
  ['withdrawn', 'Withdrawn'],
]

/** The label for each result code. A code this console does not know has no entry. */
export const RESULT_LABEL = Object.fromEntries(RESULTS)
