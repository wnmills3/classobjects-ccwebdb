import { useState } from 'react'

import { api } from '../../api'

//: view value paired with its radio label.
const VIEWS = [
  ['coins', 'Coins'],
  ['currency', 'Currency'],
]

const EMPTY_FILTERS = {
  denomination: '',
  year: '',
  mint: '',
  serialNumber: '',
  seriesYear: '',
}

/**
 * Finds an item to receive by what it is, for when the object is in hand and
 * which order it came from is not known.
 *
 * Every field here is already a filter `GET /api/inventory/{view}/search`
 * supports, so this needs no backend of its own -- it only shapes the query.
 * `status: 'ordered'` is added to every search, so the operator is never
 * offered something already received: receiving it a second time is a
 * mistake the backend would refuse with a 409, but the picker should not
 * invite it in the first place.
 *
 * A single "year" typed here becomes both `year_min` and `year_max`: the
 * backend has no single-year filter, only that range pair.
 */
export default function ItemFinder({ onPick }) {
  const [view, setView] = useState('coins')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [results, setResults] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function setField(field, value) {
    setFilters((prev) => ({ ...prev, [field]: value }))
  }

  function switchView(next) {
    setView(next)
    setResults(null)
  }

  async function find() {
    setBusy(true)
    setError('')
    const params = { status: 'ordered' }
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

    try {
      const body = await api.searchInventory(view, params)
      setResults(body.rows)
    } catch (err) {
      setError(err.message)
      setResults(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="item-finder">
      <div className="filter-grid">
        {VIEWS.map(([value, label]) => (
          <label key={value}>
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
          Denomination
          <input
            type="text"
            value={filters.denomination}
            onChange={(e) => setField('denomination', e.target.value)}
          />
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
        <p className="muted">Nothing outstanding matches those attributes.</p>
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
