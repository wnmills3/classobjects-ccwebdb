import { money } from '../../format'

function cell(row, key, kind) {
  const value = row[key]
  if (value === null || value === undefined || value === '')
    return <span className="muted">-</span>
  if (kind === 'money') return money(value)
  return String(value)
}

/**
 * The inventory results table: sortable column headers plus the rows
 * themselves.
 *
 * `selected` and `onSelect` are accepted and unused until Task 14 (bulk
 * edit needs row selection). Added now so these signatures do not have to
 * change twice. `onOpen` opens the edit form for a row's item.
 */
export default function InventoryTable({
  config,
  rows,
  current,
  apply,
  // Not used until Task 14 (selection) -- bound with a leading underscore
  // so eslint's convention for a deliberately unused binding applies
  // without changing the prop names callers pass.
  selected: _selected,
  onSelect: _onSelect,
  onOpen,
}) {
  return (
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
        {rows.map((row) => (
          <tr key={row.id}>
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
