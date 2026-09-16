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
 * **One line at a time.** The checkbox column and the bulk receipt below the
 * table were replaced by opening the picked line's own receipt dialog: the
 * panel used to render under a long table, below the fold, so clicking
 * appeared to do nothing -- the same complaint the item editor answered with
 * a modal. The receipt endpoint still takes a list and is still
 * all-or-nothing, so nothing about the request shape changed; this hands it
 * one id.
 *
 * Only a not-yet-arrived line (`ordered` or `missing`) opens: a
 * `received`/`canceled`/`returned` line is shown for context only and is
 * inert. The backend would refuse re-receiving one with a 409 anyway, but
 * the UI should not invite the click.
 *
 * `onPick(line)` takes the whole line, not its id -- the dialog names what it
 * is about ("CC-001234 - 1928 $2 Red Seal"), and the caller would otherwise
 * have to look the row back up to say so.
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

export default function OrderLines({ lines, onPick }) {
  if (lines.length === 0) {
    return <p className="muted">This order has no lines.</p>
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Item</th>
          <th>Description</th>
          <th>Cost</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {sortedLines(lines).map((line) => {
          const selectable = isSelectable(line)
          return (
            <tr
              key={line.id}
              className={selectable ? 'row-pick' : 'dim'}
              onClick={selectable ? () => onPick(line) : undefined}
            >
              <td className="mono">
                {selectable ? (
                  // A real button, so the row is reachable by keyboard and
                  // not only by mouse. `stopPropagation` keeps the row's own
                  // click from firing this a second time.
                  <button
                    type="button"
                    className="link"
                    onClick={(e) => {
                      e.stopPropagation()
                      onPick(line)
                    }}
                  >
                    {line.item_code}
                  </button>
                ) : (
                  line.item_code
                )}
              </td>
              <td>{line.source_title || line.description}</td>
              <td>{money(line.item_cost)}</td>
              <td>{line.status}</td>
              <td>{selectable && <span className="muted">Receive...</span>}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
