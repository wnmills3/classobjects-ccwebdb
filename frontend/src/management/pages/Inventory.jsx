import { useState } from 'react'

import BulkEditBar from './inventory/BulkEditBar'
import FilterPanel from './inventory/FilterPanel'
import InventoryTable from './inventory/InventoryTable'
import ItemEditDialog from './inventory/ItemEditDialog'
import ReviewPane from './inventory/ReviewPane'
import { COIN_VIEW, CURRENCY_VIEW, PAGE_SIZE } from './inventory/specs'
import { useInventorySearch } from './inventory/useInventorySearch'
import { useLinkedItem } from './inventory/useLinkedItem'

/**
 * Staff inventory browse, one screen per kind.
 *
 * Coins and currency get separate views rather than one grid with a kind
 * filter, because the columns that matter differ: a coin has a mint mark and
 * a variety, a banknote has a series letter, a seal color and its own
 * printed serial. One grid would leave most columns blank most of the time.
 *
 * The filter options come from *facets* -- value counts over the current
 * result set -- not from the full vocabulary. A real collection uses a
 * fraction of the fifty-odd grades that exist, and offering all of them
 * buries the ones actually present.
 */
function InventoryView({ config }) {
  const {
    current,
    apply,
    clear,
    refresh,
    page,
    busy,
    error,
    offset,
    itemCode,
    closeItem,
  } = useInventorySearch(config.view)
  const [editing, setEditing] = useState(null)
  const linked = useLinkedItem(config.view, itemCode)
  // A row clicked here wins over the item the address named.
  const openId = editing ?? linked.id

  // Closing the editor on the address's item takes `item` out of the
  // address, replacing that history entry rather than pushing a new one, so
  // a reload or Back does not open it again.
  function closeEditor() {
    setEditing(null)
    if (itemCode) closeItem()
  }
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
            refresh()
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
        issueDescriptions={page?.issue_descriptions ?? {}}
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
        view={config.view}
        ids={selected}
        // The page on screen, for the offer dialog: offering an item needs
        // its code, its title and its cost basis, not only its id.
        rows={rows}
        onApplied={() => {
          setSelected([])
          refresh()
        }}
        // Only the offered ids leave the selection. A selection can span
        // pages and only the rows on this one can be offered, so the ones
        // that were not stay ticked -- they are what is left to do.
        onOffered={(offered) => {
          setSelected((current) => current.filter((id) => !offered.includes(id)))
          refresh()
        }}
        onClear={() => setSelected([])}
      />

      {error && <p className="error">{error}</p>}
      {linked.problem && <p className="error">{linked.problem}</p>}
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
          sortable={page?.sortable ?? []}
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

      {openId && (
        <ItemEditDialog
          key={openId}
          itemId={openId}
          // Closed only here, on a save the server accepted. A refused one --
          // a 409 from a stale version -- leaves the dialog open with the
          // reason showing, and the draft still there to retry.
          onSaved={() => {
            closeEditor()
            refresh()
          }}
          // A split stays open to say what it made; the results behind it
          // lose the lot and gain its pieces.
          onChanged={refresh}
          onClose={closeEditor}
        />
      )}
    </section>
  )
}

/** The Coins page: the inventory screen with the coin view's columns. */
export function InventoryCoins() {
  return <InventoryView config={COIN_VIEW} />
}

/** The Currency page: the inventory screen with the banknote view's columns. */
export function InventoryCurrency() {
  return <InventoryView config={CURRENCY_VIEW} />
}
