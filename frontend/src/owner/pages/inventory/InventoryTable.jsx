import { Fragment } from 'react'
import { Link } from 'react-router-dom'

import HelpScope from '../../HelpScope'

import { date, money } from '../../../shared/format'

function cell(row, key, kind) {
  const value = row[key]
  if (value === null || value === undefined || value === '')
    return <span className="muted">-</span>
  if (kind === 'money') return money(value)
  return String(value)
}

/**
 * The order column: a link into Receiving for the purchase order an item
 * arrived on, or a dash when none is recorded.
 *
 * Named by its own number when the vendor issued one; 1,857 eBay listings
 * never got a vendor order number, so those fall back to the vendor and the
 * date ordered -- still enough to tell one order from another.
 */
function orderCell(row) {
  if (row.purchase_order_id == null) return <span className="muted">-</span>
  const label =
    row.order_number ?? `${row.vendor ?? 'order'} ${date(row.ordered_on)}`.trim()
  return (
    <Link className="mono" to={`/receiving?order=${row.purchase_order_id}`}>
      {label}
    </Link>
  )
}

/** The arrow next to the column currently being sorted on, if any. */
function sortMarker(current, key) {
  if (current.sort !== key) return ''
  return current.desc === 'true' ? ' v' : ' ^'
}

/**
 * The inventory results table: column headers plus the rows themselves.
 *
 * `selected` is an array of ids; `onSelect(ids)` replaces it wholesale, so
 * the parent holds one piece of state rather than the table holding a
 * second copy able to disagree with it. `onOpen` opens the edit form for a
 * row's item.
 *
 * `sortable` is what the server says it can sort by, sent with every page.
 * Only those headers are clickable: every header used to look sortable while
 * the server refused most of them, and a click put "cannot sort by" on the
 * page instead of sorting.
 *
 * `config.detail`, when set, names a field shown on a second line under each
 * item, spanning the columns. The description is read rather than compared,
 * and long, so it no longer takes a column of its own.
 */
export default function InventoryTable({
  config,
  rows,
  current,
  apply,
  selected,
  onSelect,
  onOpen,
  sortable = [],
}) {
  const ids = rows.map((r) => r.id)
  const allShown = ids.length > 0 && ids.every((id) => selected.includes(id))

  function toggle(id) {
    onSelect(
      selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id],
    )
  }

  return (
    <HelpScope>
      <table className="table inventory-table">
        <thead>
          <tr>
            <th className="select-cell" data-help="select_page">
              {/* Selects the current page, not the whole result set.
                "Apply to 7,591" from one click on a 50-row page is not
                something anyone means. */}
              <input
                type="checkbox"
                checked={allShown}
                onChange={() =>
                  onSelect(
                    allShown
                      ? selected.filter((id) => !ids.includes(id))
                      : [...new Set([...selected, ...ids])],
                  )
                }
              />
            </th>
            {config.columns.map(([label, key]) =>
              sortable.includes(key) ? (
                <th
                  key={key}
                  className="sortable"
                  onClick={() =>
                    apply({
                      sort: key,
                      desc:
                        current.sort === key && current.desc !== 'true' ? 'true' : '',
                    })
                  }
                >
                  {label}
                  {sortMarker(current, key)}
                </th>
              ) : (
                <th key={key}>{label}</th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const detail = config.detail ? row[config.detail] : null
            return (
              <Fragment key={row.id}>
                <tr className={detail ? 'has-detail' : undefined}>
                  <td className="select-cell" data-help="select_row">
                    <input
                      type="checkbox"
                      checked={selected.includes(row.id)}
                      onChange={() => toggle(row.id)}
                    />
                  </td>
                  {config.columns.map(([, key, kind]) => (
                    <td key={key} className={kind === 'money' ? undefined : kind}>
                      {key === 'item_code' ? (
                        <button className="link mono" onClick={() => onOpen(row.id)}>
                          {row.item_code}
                        </button>
                      ) : key === 'order_number' ? (
                        orderCell(row)
                      ) : (
                        cell(row, key, kind)
                      )}
                    </td>
                  ))}
                </tr>
                {detail && (
                  <tr className="item-detail">
                    <td className="select-cell" />
                    <td colSpan={config.columns.length}>{detail}</td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </HelpScope>
  )
}
