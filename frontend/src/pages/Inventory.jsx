import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { money } from '../format'

/**
 * Staff inventory browse, one screen per kind.
 *
 * Coins and currency get separate views rather than one grid with a kind
 * filter, because the columns that matter differ: a coin has a mint mark and a
 * variety, a banknote has a series letter, a seal colour and its own printed
 * serial. One grid would leave most columns blank most of the time.
 *
 * The filter options come from *facets* -- value counts over the current
 * result set -- not from the full vocabulary. A real collection uses a
 * fraction of the fifty-odd grades that exist, and offering all of them buries
 * the ones actually present.
 */

const COIN_VIEW = {
  view: 'coins',
  title: 'Coins & bullion',
  columns: [
    ['Code', 'item_code', 'mono'],
    ['Title', 'title'],
    ['Kind', 'item_kind'],
    ['Year', 'year_start'],
    ['Mint', 'mint_mark'],
    ['Grade', 'grade'],
    ['Metal', 'metal'],
    ['Fine ozt', 'fine_weight_ozt'],
    ['Qty', 'storage_quantity'],
    ['Cost', 'total_cost', 'money'],
    ['Status', 'status'],
  ],
  facetFilters: [
    ['Kind', 'kind', 'item_kind'],
    ['Metal', 'metal', 'metal'],
    ['Grade', 'grade', 'grade'],
    ['Mint', 'mint', 'mint_mark'],
    ['Country', 'country', 'country'],
    ['Status', 'status', 'status'],
    ['Disposition', 'disposition', 'disposition'],
  ],
}

const CURRENCY_VIEW = {
  view: 'currency',
  title: 'Currency',
  columns: [
    ['Code', 'item_code', 'mono'],
    ['Title', 'title'],
    ['Denomination', 'denomination_label'],
    ['Series', 'series_year'],
    ['Letter', 'series_letter'],
    ['Seal', 'seal_color'],
    ['District', 'fed_district_letter'],
    ['Serial', 'serial_number', 'mono'],
    ['Fr#', 'friedberg_number'],
    ['Grade', 'grade'],
    ['Cost', 'total_cost', 'money'],
    ['Status', 'status'],
  ],
  facetFilters: [
    ['Note type', 'note_type', 'note_type'],
    ['Seal', 'seal_color', 'seal_color'],
    ['District', 'fed_district', 'fed_district_letter'],
    ['Grade', 'grade', 'grade'],
    ['Series', 'series_year', 'series_year'],
    ['Status', 'status', 'status'],
    ['Disposition', 'disposition', 'disposition'],
  ],
}

const PAGE_SIZE = 50

function cell(row, key, kind) {
  const value = row[key]
  if (value === null || value === undefined || value === '') return <span className="muted">-</span>
  if (kind === 'money') return money(value)
  return String(value)
}

function InventoryView({ config }) {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)

  // The URL is the source of truth for the search, so a filtered view can be
  // bookmarked, shared with someone, or survive a reload.
  const current = Object.fromEntries(params.entries())
  const offset = Number(current.offset ?? 0)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const body = await api.searchInventory(config.view, {
        ...current,
        facets: true,
        limit: PAGE_SIZE,
      })
      setPage(body)
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.view, params.toString()])

  useEffect(() => {
    load()
  }, [load])

  function apply(changes) {
    const next = { ...current, ...changes }
    // Any change to the filters returns to the first page: staying on page 7
    // of a result set that no longer has one is disorienting.
    if (!('offset' in changes)) delete next.offset
    Object.keys(next).forEach((k) => {
      if (next[k] === '' || next[k] === null || next[k] === undefined) delete next[k]
    })
    setParams(next)
  }

  const facets = page?.facets ?? {}
  const total = page?.total ?? 0
  const shown = page?.rows?.length ?? 0

  return (
    <section>
      <h1>{config.title}</h1>

      <div className="search-panel">
        <input
          className="search-text"
          placeholder="Search title, item code, notes..."
          defaultValue={current.q ?? ''}
          onKeyDown={(e) => {
            if (e.key === 'Enter') apply({ q: e.target.value })
          }}
          onBlur={(e) => apply({ q: e.target.value })}
        />

        <div className="filter-grid">
          {config.facetFilters.map(([label, param, facetKey]) => {
            const options = facets[facetKey] ?? []
            return (
              <label key={param}>
                {label}
                <select
                  value={current[param] ?? ''}
                  onChange={(e) => apply({ [param]: e.target.value })}
                  disabled={options.length === 0}
                >
                  <option value="">Any</option>
                  {options.map((o) => (
                    <option key={String(o.value)} value={String(o.value)}>
                      {String(o.value)} ({o.count})
                    </option>
                  ))}
                </select>
              </label>
            )
          })}

          <label>
            Year from
            <input
              type="number"
              defaultValue={current.year_min ?? ''}
              onBlur={(e) => apply({ year_min: e.target.value })}
            />
          </label>
          <label>
            Year to
            <input
              type="number"
              defaultValue={current.year_max ?? ''}
              onBlur={(e) => apply({ year_max: e.target.value })}
            />
          </label>
        </div>

        <div className="row">
          <button className="link" onClick={() => setParams({})}>
            Clear filters
          </button>
          <span className="muted">
            {busy ? 'Searching...' : `${total.toLocaleString()} matching`}
          </span>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {!busy && total === 0 && (
        <p className="muted">Nothing matches those filters.</p>
      )}

      {shown > 0 && (
        <table className="table inventory-table">
          <thead>
            <tr>
              {config.columns.map(([label, key]) => (
                <th
                  key={key}
                  className="sortable"
                  onClick={() =>
                    apply({
                      sort: key,
                      desc: current.sort === key && current.desc !== 'true' ? 'true' : '',
                    })
                  }
                >
                  {label}
                  {current.sort === key ? (current.desc === 'true' ? ' v' : ' ^') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row) => (
              <tr key={row.id}>
                {config.columns.map(([, key, kind]) => (
                  <td key={key} className={kind === 'mono' ? 'mono' : undefined}>
                    {cell(row, key, kind)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {total > PAGE_SIZE && (
        <div className="row pager">
          <button
            disabled={offset === 0}
            onClick={() => apply({ offset: Math.max(0, offset - PAGE_SIZE) })}
          >
            Previous
          </button>
          <span className="muted">
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total.toLocaleString()}
          </span>
          <button
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => apply({ offset: offset + PAGE_SIZE })}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}

export function InventoryCoins() {
  return <InventoryView config={COIN_VIEW} />
}

export function InventoryCurrency() {
  return <InventoryView config={CURRENCY_VIEW} />
}
