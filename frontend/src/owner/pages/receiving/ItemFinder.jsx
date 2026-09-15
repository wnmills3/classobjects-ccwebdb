import { useRef, useState } from 'react'

import { useReference } from '../../../shared/reference-context'
import { api } from '../../api'

//: view value paired with its radio label.
const VIEWS = [
  ['coins', 'Coins'],
  ['currency', 'Currency'],
]

//: The statuses that mean "has not arrived yet." `missing` is included
//: alongside `ordered` because a parcel written off as missing sometimes
//: turns up -- see `OutstandingList`'s matching list.
const OUTSTANDING_STATUSES = ['ordered', 'missing']

const EMPTY_FILTERS = {
  status: '',
  denomination: '',
  year: '',
  mint: '',
  serialNumber: '',
  seriesYear: '',
}

/**
 * Finds an item by what it is, for when the object is in hand and which order
 * it came from is not known.
 *
 * Every field here is already a filter `GET /api/inventory/{view}/search`
 * supports, so this needs no backend of its own -- it only shapes the query.
 *
 * By default it finds only what has not arrived: `status=ordered` or
 * `status=missing` -- a parcel written off as missing and later turning up is
 * exactly what that code exists for. The search endpoint's `status` filter
 * compares to one value, so that default is two requests, merged. Choosing a
 * status searches that one instead, so an item already recorded as received
 * can be found; receiving it a second time is still refused by the backend,
 * which names when it arrived.
 *
 * Status and denomination are chosen from their vocabularies rather than
 * typed. The filters compare codes, so a typed "Cent" never matched
 * `usd_coin_0_01` -- a denomination box that could only ever find nothing.
 *
 * A single "year" typed here becomes both `year_min` and `year_max`: the
 * backend has no single-year filter, only that range pair.
 *
 * `cancelRef` guards against a stale response the same way `Receiving.jsx`
 * guards a stale order fetch: a search in flight sets a `cancelled` flag a
 * later event can flip before the response lands. Switching the view is the
 * case that matters -- a coins row must never render, let alone be clickable
 * into `onPick`, once the fields on screen are currency's -- and starting a
 * fresh search invalidates whatever the button's own last click kicked off,
 * so two in-flight requests can never both write to `results`.
 */
export default function ItemFinder({ onPick }) {
  const [view, setView] = useState('coins')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [results, setResults] = useState(null)
  //: The status the displayed results were searched for, so the "nothing
  //: matches" message describes that search, not a status picked since.
  const [searchedStatus, setSearchedStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const cancelRef = useRef(null)
  const statuses = useReference('item_status')
  const denominations = useReference('denomination')

  function setField(field, value) {
    setFilters((prev) => ({ ...prev, [field]: value }))
  }

  function invalidatePendingSearch() {
    cancelRef.current?.()
    cancelRef.current = null
  }

  function switchView(next) {
    invalidatePendingSearch()
    setBusy(false)
    setView(next)
    setResults(null)
  }

  async function find() {
    invalidatePendingSearch()
    let cancelled = false
    cancelRef.current = () => {
      cancelled = true
    }

    setBusy(true)
    setError('')
    const params = {}
    if (filters.denomination) params.denomination = filters.denomination
    if (view === 'coins') {
      if (filters.year) {
        params.year_min = filters.year
        params.year_max = filters.year
      }
      if (filters.mint) params.mint = filters.mint
    } else {
      if (filters.serialNumber) params.serial_number = filters.serialNumber
      if (filters.seriesYear) params.series_year = filters.seriesYear
    }
    const wanted = filters.status ? [filters.status] : OUTSTANDING_STATUSES

    try {
      const bodies = await Promise.all(
        wanted.map((status) => api.searchInventory(view, { ...params, status })),
      )
      if (cancelled) return
      setResults(bodies.flatMap((body) => body.rows))
      setSearchedStatus(filters.status)
    } catch (err) {
      if (cancelled) return
      setError(err.message)
      setResults(null)
    } finally {
      if (!cancelled) setBusy(false)
    }
  }

  return (
    <div className="item-finder">
      <div className="filter-grid">
        {VIEWS.map(([value, label]) => (
          <label key={value} className="checkbox">
            <input
              type="radio"
              name="item-finder-view"
              value={value}
              checked={view === value}
              onChange={() => switchView(value)}
            />
            {label}
          </label>
        ))}
      </div>

      <div className="filter-grid">
        <label>
          Status
          <select
            value={filters.status}
            onChange={(e) => setField('status', e.target.value)}
          >
            <option value="">Not yet arrived</option>
            {(statuses ?? []).map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
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

        {view === 'coins' ? (
          <>
            <label>
              Year
              <input
                type="text"
                value={filters.year}
                onChange={(e) => setField('year', e.target.value)}
              />
            </label>
            <label>
              Mint
              <input
                type="text"
                value={filters.mint}
                onChange={(e) => setField('mint', e.target.value)}
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Serial number
              <input
                type="text"
                value={filters.serialNumber}
                onChange={(e) => setField('serialNumber', e.target.value)}
              />
            </label>
            <label>
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
        Find
      </button>

      {error && <p className="error">{error}</p>}

      {results && results.length === 0 && (
        <p className="muted">
          {searchedStatus
            ? 'Nothing in that status matches those attributes.'
            : 'Nothing outstanding matches those attributes.'}
        </p>
      )}

      {results && results.length > 0 && (
        <ul className="order-picker">
          {results.map((row) => (
            <li key={row.id} className="order-row" onClick={() => onPick(row.id)}>
              <span className="mono">{row.item_code}</span> {row.description}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
