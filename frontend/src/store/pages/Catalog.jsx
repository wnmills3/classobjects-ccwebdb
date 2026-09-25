import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../../shared/api'
import { useReference } from '../../shared/reference-context'
import { useCart } from '../cart-context'
import { money } from '../../shared/format'
import { summarize, thumbnail } from './lot-entry'

const PAGE_SIZE = 12

/**
 * The catalog grid. Some cards are a coin; some are a LOT of coins.
 *
 * A lot carries no photograph and no country, year or grade of its own -- no
 * single value of any of them describes a group -- so a card that read only
 * those fields showed a lot as an untitled-looking, unpriced-looking,
 * pictureless box with a blank line where the description goes. `lot-entry.js`
 * answers both questions from `members` instead.
 *
 * The item filters above cannot match a lot at all, and that is deliberate
 * rather than a gap: each one compares a column of the inventory row a lot
 * listing does not have, so a page filtered by grade or year holds no lots.
 * `q` still finds one, through the listing's own title.
 */

export default function Catalog() {
  const [result, setResult] = useState(null)
  const [filters, setFilters] = useState({ q: '', kind: '', in_stock: false })
  const kinds = useReference('item_kind')
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')

  const { add } = useCart()

  // The request is keyed by what was asked for, so "loading" can be derived
  // rather than tracked separately, and a slow response for an old filter
  // cannot land after a newer one and overwrite it.
  const key = JSON.stringify({ ...filters, offset })

  useEffect(() => {
    let cancelled = false
    api
      .listCatalog({ ...filters, limit: PAGE_SIZE, offset })
      .then((result) => {
        if (cancelled) return
        setResult({ key, page: result })
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  function applyFilter(patch) {
    setOffset(0)
    setFilters((f) => ({ ...f, ...patch }))
  }

  // Derived rather than stored: loading is exactly "the page on screen is not
  // the one being asked for".
  const busy = result?.key !== key
  const page = result?.page ?? { items: [], total: 0, limit: PAGE_SIZE, offset: 0 }
  const shown = page.offset + page.items.length

  return (
    <section>
      <h1>Catalog</h1>

      <div className="filters">
        <input
          type="search"
          placeholder="Search title, SKU, country..."
          value={filters.q}
          onChange={(e) => applyFilter({ q: e.target.value })}
        />
        {/* Read from the vocabulary rather than repeated here, so a new
            item_kind shows up in the filter without a frontend change. */}
        <select
          value={filters.kind}
          onChange={(e) => applyFilter({ kind: e.target.value })}
        >
          <option value="">All types</option>
          {(kinds ?? []).map((entry) => (
            <option key={entry.code} value={entry.code}>
              {entry.label}
            </option>
          ))}
        </select>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={filters.in_stock}
            onChange={(e) => applyFilter({ in_stock: e.target.checked })}
          />
          {/* */}
          In stock only
        </label>
      </div>

      {error && <p className="error">{error}</p>}
      {busy && <p className="muted">Loading...</p>}
      {!busy && page.items.length === 0 && (
        <p className="muted">No items match those filters.</p>
      )}

      <div className="grid">
        {page.items.map((coin) => {
          const thumb = thumbnail(coin)
          return (
            <article key={coin.id} className="card">
              {/* Most of a real collection is unphotographed, so the card has
                to look deliberate with no image rather than broken. A lot's
                picture is one of its coins, and the alt text says so. */}
              {thumb ? (
                <Link to={`/coins/${coin.id}`} className="card-thumb">
                  <img src={thumb.url} alt={thumb.alt} loading="lazy" />
                </Link>
              ) : (
                <div className="card-thumb card-thumb-empty" aria-hidden="true" />
              )}
              <div className="card-body">
                <h3>
                  <Link to={`/coins/${coin.id}`}>{coin.title}</Link>
                </h3>
                <p className="muted small">
                  {/* A coin's country, year and grade -- or, for a lot, how
                    many coins it is, because the price is a group's. */}
                  {summarize(coin)}
                </p>
                <p className="price">{money(coin.price, coin.currency)}</p>
                <p className="muted small">
                  {coin.quantity_available > 0
                    ? `${coin.quantity_available} available`
                    : 'Sold out'}
                </p>
              </div>
              <button
                disabled={coin.quantity_available === 0}
                onClick={() => add(coin, 1)}
              >
                {coin.quantity_available === 0 ? 'Sold out' : 'Add to cart'}
              </button>
            </article>
          )
        })}
      </div>

      {page.total > PAGE_SIZE && (
        <div className="pager">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Previous
          </button>
          <span className="muted">
            {page.offset + 1}-{shown} of {page.total}
          </span>
          <button
            disabled={shown >= page.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}
