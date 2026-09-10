import { money } from '../../../shared/format'

/**
 * Every line on a purchase order, not-yet-arrived ones first.
 *
 * Renamed from `OutstandingList`, which showed only lines still awaiting
 * arrival. Two things made that a lie: a `missing` line -- paid for, not
 * cancelled, never arrived -- reports the same `outstanding` count as
 * `ordered` (see `GET /api/purchase-orders`) and belongs next to it rather
 * than hidden; and hiding every arrived line left no way to see what an
 * order contained once it was fully received. So every line is shown now,
 * sorted so what still needs attention reads first, with each line's status
 * making the two groups visually distinguishable (a dimmed row for one
 * already resolved).
 *
 * Only a not-yet-arrived line (`ordered` or `missing`) is selectable -- a
 * `received`/`canceled`/`returned` line is shown for context only and its
 * checkbox is disabled, never checked, and skipped by "select all". The
 * backend would refuse re-receiving one with a 409 anyway, but the UI
 * should not invite the click.
 *
 * `selected` is an array of line ids; `onChange(ids)` replaces it wholesale,
 * the same convention `InventoryTable` uses, so the parent holds one piece of
 * state rather than this list holding a second copy able to disagree with it.
 */
const NOT_ARRIVED_STATUSES = ['ordered', 'missing']

function isSelectable(line) {
  return NOT_ARRIVED_STATUSES.includes(line.status)
}

//: Not-yet-arrived lines first, then everything else; item code within each
//: group for a stable, sensible order.
function sortedLines(lines) {
  return [...lines].sort((a, b) => {
    const byGroup = Number(isSelectable(b)) - Number(isSelectable(a))
    if (byGroup !== 0) return byGroup
    return a.item_code < b.item_code ? -1 : a.item_code > b.item_code ? 1 : 0
  })
}

export default function OrderLines({ lines, selected, onChange }) {
  if (lines.length === 0) {
    return <p className="muted">This order has no lines.</p>
  }

  const ordered = sortedLines(lines)
  const selectableIds = ordered.filter(isSelectable).map((line) => line.id)
  const allChecked =
    selectableIds.length > 0 && selectableIds.every((id) => selected.includes(id))

  function toggle(id) {
    onChange(
      selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id],
    )
  }

  function toggleAll() {
    onChange(
      allChecked
        ? selected.filter((id) => !selectableIds.includes(id))
        : [...new Set([...selected, ...selectableIds])],
    )
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th className="select-cell">
            <input
              type="checkbox"
              aria-label="Select all outstanding lines"
              checked={allChecked}
              disabled={selectableIds.length === 0}
              onChange={toggleAll}
            />
          </th>
          <th>Item</th>
          <th>Description</th>
          <th>Cost</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {ordered.map((line) => {
          const selectable = isSelectable(line)
          return (
            <tr key={line.id} className={selectable ? '' : 'dim'}>
              <td className="select-cell">
                <input
                  type="checkbox"
                  checked={selectable && selected.includes(line.id)}
                  disabled={!selectable}
                  onChange={() => toggle(line.id)}
                />
              </td>
              <td className="mono">{line.item_code}</td>
              <td>{line.description}</td>
              <td>{money(line.item_cost)}</td>
              <td>{line.status}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
