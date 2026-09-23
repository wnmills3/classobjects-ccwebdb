import { useEffect, useRef, useState } from 'react'

import { useReference } from '../../../shared/reference-context'
import { api } from '../../api'
import HelpScope from '../../HelpScope'

//: kind value paired with its radio label. `any` searches both views -- one
//: parcel can hold coins and notes, and the coins view is everything that
//: is not a note, so the two together are every item.
const KINDS = [
  ['any', 'Any'],
  ['coins', 'Coins'],
  ['currency', 'Currency'],
]

//: The statuses that mean "has not arrived yet." `missing` is included
//: alongside `ordered` because a parcel written off as missing sometimes
//: turns up.
const OUTSTANDING_STATUSES = ['ordered', 'missing']

//: The status select's value for "no status filter at all" -- the whole of
//: an order, received lines included, the way the old order view showed it.
const ANY_STATUS = 'any'

const EMPTY_FILTERS = {
  status: '',
  denomination: '',
  orderNumber: '',
  year: '',
  mint: '',
  serialNumber: '',
  seriesYear: '',
}

/**
 * The requests one search makes: every view the kind covers, times every
 * status it asks for. The search endpoint filters one view and one status
 * value per request, so "any kind, not yet arrived" is four, merged.
 */
async function runSearch({ kind, filters, orderId = null }) {
  const views = kind === 'any' ? ['coins', 'currency'] : [kind]
  const statuses =
    filters.status === ANY_STATUS
      ? [null]
      : filters.status
        ? [filters.status]
        : OUTSTANDING_STATUSES
  const shared = {}
  if (filters.denomination) shared.denomination = filters.denomination
  if (orderId != null) shared.purchase_order_id = orderId
  else if (filters.orderNumber.trim()) shared.order_number = filters.orderNumber.trim()

  const requests = []
  for (const view of views) {
    const params = { ...shared }
    // Kind-specific fields only once that kind is chosen: they are shown
    // only then, and the other view would refuse them as unknown filters.
    if (kind === 'coins') {
      if (filters.year) {
        params.year_min = filters.year
        params.year_max = filters.year
      }
      if (filters.mint) params.mint = filters.mint
    } else if (kind === 'currency') {
      if (filters.serialNumber) params.serial_number = filters.serialNumber
      if (filters.seriesYear) params.series_year = filters.seriesYear
    }
    for (const status of statuses) {
      requests.push(api.searchInventory(view, status ? { ...params, status } : params))
    }
  }
  const bodies = await Promise.all(requests)
  return bodies.flatMap((body) => body.rows)
}

/**
 * Run `query`, guarded against a stale response, and show what it finds.
 *
 * Module-level and handed the component's setters and refs, rather than a
 * function in the component body, so the effects that call it depend only on
 * what actually triggers them: setters and refs are stable, a body function
 * is new every render. Returns the cancel function, for an effect's cleanup.
 */
function searchInto(
  query,
  { cancelRef, lastQueryRef, setResults, setSearchedStatus, setError, setBusy },
) {
  cancelRef.current?.()
  let cancelled = false
  cancelRef.current = () => {
    cancelled = true
  }
  lastQueryRef.current = query
  runSearch(query)
    .then((rows) => {
      if (cancelled) return
      setResults(rows)
      setSearchedStatus(query.filters.status)
      setError('')
    })
    .catch((err) => {
      if (cancelled) return
      setError(err.message)
      setResults(null)
    })
    .finally(() => {
      if (!cancelled) setBusy(false)
    })
  return () => {
    cancelled = true
  }
}

/**
 * Receiving's one search: find what arrived by order number, by what it
 * is, or both.
 *
 * Replaced the page's "By order" / "By item" choice (2026-09-23): an order
 * number field that takes part of the number does what picking an order
 * from a list did, and the same form still finds an item in hand whose
 * order is not known. Every field is a filter `GET
 * /api/inventory/{view}/search` supports -- `order_number` matches part of
 * the number, case-insensitively -- so this only shapes the query.
 *
 * By default it finds only what has not arrived (`ordered` or `missing`).
 * Choosing a status searches that one; "Any status" drops the filter, which
 * is how to see a whole order, received lines included. Receiving something
 * a second time is still refused by the backend, which names when it
 * arrived.
 *
 * `orderId` (with `initialOrderNumber` to show in the field) is how a link
 * naming one order (`?order=`) opens here: it searches at once, by the
 * order's id -- its number is neither unique across vendors nor always
 * recorded, so matching the number would show other orders' items or none.
 * The id stays in force while the field still shows that order's number;
 * typing another number searches by number as usual. `epoch`, bumped by the page
 * after each receipt, repeats the last search, so the item just received
 * leaves the "not yet arrived" list instead of staying clickable.
 *
 * `cancelRef` guards against a stale response: a search in flight sets a
 * `cancelled` flag a later search or kind switch flips before it lands, so
 * two in-flight requests can never both write to `results`.
 *
 * `onPick(row)` receives the whole result row: the receipt dialog it opens
 * names what it is about.
 */
export default function ItemFinder({
  onPick,
  orderId = null,
  initialOrderNumber = '',
  epoch = 0,
}) {
  const [kind, setKind] = useState('any')
  const [filters, setFilters] = useState({
    ...EMPTY_FILTERS,
    orderNumber: initialOrderNumber,
  })
  const [results, setResults] = useState(null)
  //: The status the displayed results were searched for, so the "nothing
  //: matches" message describes that search, not a status picked since.
  const [searchedStatus, setSearchedStatus] = useState('')
  const [error, setError] = useState('')
  // True from the start when a linked order searches on mount, rather than
  // set inside that effect.
  const [busy, setBusy] = useState(orderId != null)
  const cancelRef = useRef(null)
  const lastQueryRef = useRef(null)
  const statuses = useReference('item_status')
  const denominations = useReference('denomination')

  function setField(field, value) {
    setFilters((prev) => ({ ...prev, [field]: value }))
  }

  function invalidatePendingSearch() {
    cancelRef.current?.()
    cancelRef.current = null
  }

  function find() {
    setBusy(true)
    // Still the linked order while its number is what the field shows.
    const linked = orderId != null && filters.orderNumber === initialOrderNumber
    searchInto(
      { kind, filters, orderId: linked ? orderId : null },
      { cancelRef, lastQueryRef, setResults, setSearchedStatus, setError, setBusy },
    )
  }

  function switchKind(next) {
    invalidatePendingSearch()
    setBusy(false)
    setKind(next)
    setResults(null)
  }

  // A linked order searches once, on arrival. The page remounts this (by
  // key) for a different order, so the initial query never changes here.
  const [initialQuery] = useState(() =>
    orderId != null
      ? {
          kind: 'any',
          filters: { ...EMPTY_FILTERS, orderNumber: initialOrderNumber },
          orderId,
        }
      : null,
  )
  useEffect(() => {
    if (!initialQuery) return undefined
    return searchInto(initialQuery, {
      cancelRef,
      lastQueryRef,
      setResults,
      setSearchedStatus,
      setError,
      setBusy,
    })
  }, [initialQuery])

  // After a receipt: the same search again, so what just arrived drops out.
  useEffect(() => {
    if (epoch === 0 || !lastQueryRef.current) return undefined
    return searchInto(lastQueryRef.current, {
      cancelRef,
      lastQueryRef,
      setResults,
      setSearchedStatus,
      setError,
      setBusy,
    })
  }, [epoch])

  return (
    <HelpScope>
      <div className="item-finder">
        <div className="filter-grid" data-help="search_kind">
          {KINDS.map(([value, label]) => (
            <label key={value} className="checkbox">
              <input
                type="radio"
                name="item-finder-kind"
                value={value}
                checked={kind === value}
                onChange={() => switchKind(value)}
              />
              {label}
            </label>
          ))}
        </div>

        <div className="filter-grid">
          <label data-help="order_number">
            Order number
            <input
              type="text"
              value={filters.orderNumber}
              onChange={(e) => setField('orderNumber', e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') find()
              }}
            />
          </label>
          <label data-help="status">
            Status
            <select
              value={filters.status}
              onChange={(e) => setField('status', e.target.value)}
            >
              <option value="">Not yet arrived</option>
              <option value={ANY_STATUS}>Any status</option>
              {(statuses ?? []).map((s) => (
                <option key={s.code} value={s.code}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label data-help="denomination">
            Denomination
            <select
              value={filters.denomination}
              onChange={(e) => setField('denomination', e.target.value)}
            >
              <option value="">Any</option>
              {(denominations ?? []).map((d) => (
                <option key={d.code} value={d.code}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>

          {kind === 'coins' && (
            <>
              <label data-help="year">
                Year
                <input
                  type="text"
                  value={filters.year}
                  onChange={(e) => setField('year', e.target.value)}
                />
              </label>
              <label data-help="mint">
                Mint
                <input
                  type="text"
                  value={filters.mint}
                  onChange={(e) => setField('mint', e.target.value)}
                />
              </label>
            </>
          )}
          {kind === 'currency' && (
            <>
              <label data-help="serial_number">
                Serial number
                <input
                  type="text"
                  value={filters.serialNumber}
                  onChange={(e) => setField('serialNumber', e.target.value)}
                />
              </label>
              <label data-help="series_year">
                Series year
                <input
                  type="text"
                  value={filters.seriesYear}
                  onChange={(e) => setField('seriesYear', e.target.value)}
                />
              </label>
            </>
          )}
        </div>

        <button disabled={busy} onClick={find}>
          {busy ? 'Finding...' : 'Find'}
        </button>

        {error && <p className="error">{error}</p>}

        {results && results.length === 0 && (
          <p className="muted">
            {searchedStatus
              ? 'Nothing in that status matches.'
              : 'Nothing outstanding matches.'}
          </p>
        )}

        {results && results.length > 0 && (
          <ul className="order-picker">
            {results.map((row) => (
              <li key={row.id}>
                <button type="button" className="order-row" onClick={() => onPick(row)}>
                  <span className="mono">{row.item_code}</span> {row.description}
                  {row.order_number && (
                    <span className="muted">
                      {' '}
                      &middot; {row.order_number}
                      {row.vendor ? ` · ${row.vendor}` : ''}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </HelpScope>
  )
}
