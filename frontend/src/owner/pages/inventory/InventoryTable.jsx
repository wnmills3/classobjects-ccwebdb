import { money } from '../../../shared/format'

function cell(row, key, kind) {
  const value = row[key]
  if (value === null || value === undefined || value === '')
    return <span className="muted">-</span>
  if (kind === 'money') return money(value)
  return String(value)
}

/** The arrow next to the column currently being sorted on, if any. */
function sortMarker(current, key) {
  if (current.sort !== key) return ''
  return current.desc === 'true' ? ' v' : ' ^'
}

/**
 * The inventory results table: sortable column headers plus the rows
 * themselves.
 *
 * `selected` is an array of ids; `onSelect(ids)` replaces it wholesale, so
 * the parent holds one piece of state rather than the table holding a
 * second copy able to disagree with it. `onOpen` opens the edit form for a
 * row's item.
 */
export default function InventoryTable({
  config,
  rows,
  current,
  apply,
  selected,
  onSelect,
  onOpen,
}) {
  const ids = rows.map((r) => r.id)
  const allShown = ids.length > 0 && ids.every((id) => selected.includes(id))

  function toggle(id) {
    onSelect(
      selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id],
    )
  }

  return (
    <table className="table inventory-table">
      <thead>
        <tr>
          <th className="select-cell">
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
              {sortMarker(current, key)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td className="select-cell">
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
                ) : (
                  cell(row, key, kind)
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
