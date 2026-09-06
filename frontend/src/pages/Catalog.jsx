import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import { useCart } from '../cart'
import { money } from '../format'

const PAGE_SIZE = 12

export default function Catalog() {
  const [page, setPage] = useState({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 })
  const [filters, setFilters] = useState({ q: '', kind: '', in_stock: false })
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const { add } = useCart()

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const result = await api.listCatalog({
        ...filters,
        limit: PAGE_SIZE,
        offset,
      })
      setPage(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }, [filters, offset])

  useEffect(() => {
    load()
  }, [load])

  function applyFilter(patch) {
    setOffset(0)
    setFilters((f) => ({ ...f, ...patch }))
  }

  const shown = page.offset + page.items.length

  return (
    <section>
      <h1>Catalogue</h1>

      <div className="filters">
        <input
          type="search"
          placeholder="Search title, SKU, country..."
          value={filters.q}
          onChange={(e) => applyFilter({ q: e.target.value })}
        />
        <select
          value={filters.kind}
          onChange={(e) => applyFilter({ kind: e.target.value })}
        >
          <option value="">All types</option>
          <option value="coin">Coins</option>
          <option value="currency">Currency</option>
          <option value="bullion">Bullion</option>
          <option value="set">Sets</option>
          <option value="medal">Medals</option>
          <option value="token">Tokens</option>
        </select>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={filters.in_stock}
            onChange={(e) => applyFilter({ in_stock: e.target.checked })}
          />
          In stock only
        </label>
      </div>

      {error && <p className="error">{error}</p>}
      {busy && <p className="muted">Loading...</p>}
      {!busy && page.items.length === 0 && (
        <p className="muted">No items match those filters.</p>
      )}

      <div className="grid">
        {page.items.map((coin) => (
          <article key={coin.id} className="card">
            {/* Most of a real collection is unphotographed, so the card has
                to look deliberate with no image rather than broken. */}
            {coin.thumbnail_url ? (
              <Link to={`/coins/${coin.id}`} className="card-thumb">
                <img src={coin.thumbnail_url} alt={coin.title} loading="lazy" />
              </Link>
            ) : (
              <div className="card-thumb card-thumb-empty" aria-hidden="true" />
            )}
            <div className="card-body">
              <h3>
                <Link to={`/coins/${coin.id}`}>{coin.title}</Link>
              </h3>
              <p className="muted small">
                {[coin.country, coin.year_start, coin.grade].filter(Boolean).join(' - ')}
              </p>
              <p className="price">{money(coin.price)}</p>
              <p className="muted small">
                {coin.quantity_available > 0 ? `${coin.quantity_available} available` : 'Sold out'}
              </p>
            </div>
            <button
              disabled={coin.quantity_available === 0}
              onClick={() => add(coin, 1)}
            >
              {coin.quantity_available === 0 ? 'Sold out' : 'Add to cart'}
            </button>
          </article>
        ))}
      </div>

      {page.total > PAGE_SIZE && (
        <div className="pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </button>
          <span className="muted">
            {page.offset + 1}-{shown} of {page.total}
          </span>
          <button disabled={shown >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
            Next
          </button>
        </div>
      )}
    </section>
  )
}
