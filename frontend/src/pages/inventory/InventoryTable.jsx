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
 * edit needs row selection); `onOpen` is unused until Task 13 (the edit
 * form). Added now so these signatures do not have to change twice.
 */
export default function InventoryTable({
  config,
  rows,
  current,
  apply,
  // Not used until Task 14 (selection) and Task 13 (opening the edit
  // form) -- bound with a leading underscore so eslint's convention for a
  // deliberately unused binding applies without changing the prop names
  // callers pass.
  selected: _selected,
  onSelect: _onSelect,
  onOpen: _onOpen,
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
                {cell(row, key, kind)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
