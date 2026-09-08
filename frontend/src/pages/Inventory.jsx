import { useState } from 'react'

import BulkEditBar from './inventory/BulkEditBar'
import FilterPanel from './inventory/FilterPanel'
import InventoryTable from './inventory/InventoryTable'
import ItemEditForm from './inventory/ItemEditForm'
import ReviewPane from './inventory/ReviewPane'
import { COIN_VIEW, CURRENCY_VIEW, PAGE_SIZE } from './inventory/specs'
import { useInventorySearch } from './inventory/useInventorySearch'

/**
 * Staff inventory browse, one screen per kind.
 *
 * Coins and currency get separate views rather than one grid with a kind
 * filter, because the columns that matter differ: a coin has a mint mark and
 * a variety, a banknote has a series letter, a seal colour and its own
 * printed serial. One grid would leave most columns blank most of the time.
 *
 * The filter options come from *facets* -- value counts over the current
 * result set -- not from the full vocabulary. A real collection uses a
 * fraction of the fifty-odd grades that exist, and offering all of them
 * buries the ones actually present.
 */
function InventoryView({ config }) {
  const { current, apply, clear, page, busy, error, offset } = useInventorySearch(
    config.view,
  )
  const [editing, setEditing] = useState(null)
  const [selected, setSelected] = useState([])
  const [reviewing, setReviewing] = useState(null)

  const total = page?.total ?? 0
  const rows = page?.rows ?? []

  // Two renderings of one result set, not both at once. While review is open
  // the filters, sorting, paging and bulk bar are gone -- changing any of
  // them would reorder the table behind the pane, and a second edit form
  // opened from a row would let two saves race with versions read at
  // different moments. The queue keeps the query; leaving review is what
  // returns you to it.
  if (reviewing) {
    return (
      <section>
        <h1>{config.title}</h1>
        <ReviewPane
          ids={reviewing}
          onClose={() => {
            setReviewing(null)
            apply({})
          }}
        />
      </section>
    )
  }

  return (
    <section>
      <h1>{config.title}</h1>

      <FilterPanel
        config={config}
        current={current}
        apply={apply}
        clear={clear}
        facets={page?.facets ?? {}}
        issues={page?.issues ?? {}}
        total={total}
        busy={busy}
      />

      <button
        disabled={rows.length === 0}
        onClick={() => setReviewing(rows.map((r) => r.id))}
      >
        Review these {rows.length}
      </button>

      <BulkEditBar
        ids={selected}
        onApplied={() => {
          setSelected([])
          apply({})
        }}
        onClear={() => setSelected([])}
      />

      {error && <p className="error">{error}</p>}
      {!busy && total === 0 && <p className="muted">Nothing matches those filters.</p>}

      {rows.length > 0 && (
        <InventoryTable
          config={config}
          rows={rows}
          current={current}
          apply={apply}
          selected={selected}
          onSelect={setSelected}
          onOpen={setEditing}
        />
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
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of{' '}
            {total.toLocaleString()}
          </span>
          <button
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => apply({ offset: offset + PAGE_SIZE })}
          >
            Next
          </button>
        </div>
      )}

      {editing && (
        <ItemEditForm
          key={editing}
          itemId={editing}
          onSaved={() => apply({})}
          onClose={() => setEditing(null)}
        />
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
