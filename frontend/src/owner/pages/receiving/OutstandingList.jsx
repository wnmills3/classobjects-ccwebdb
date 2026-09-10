import { money } from '../../../shared/format'

/**
 * The lines of a purchase order that have not arrived yet.
 *
 * A line already marked received has nothing left to do here, so it is
 * filtered out rather than shown disabled -- the owner is receiving a
 * shipment, not auditing the whole order.
 *
 * `selected` is an array of line ids; `onChange(ids)` replaces it wholesale,
 * the same convention `InventoryTable` uses, so the parent holds one piece of
 * state rather than this list holding a second copy able to disagree with it.
 */
export default function OutstandingList({ lines, selected, onChange }) {
  const pending = lines.filter((line) => line.status === 'ordered')
  const ids = pending.map((line) => line.id)
  const allChecked = ids.length > 0 && ids.every((id) => selected.includes(id))

  function toggle(id) {
    onChange(
      selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id],
    )
  }

  function toggleAll() {
    onChange(
      allChecked
        ? selected.filter((id) => !ids.includes(id))
        : [...new Set([...selected, ...ids])],
    )
  }

  if (pending.length === 0) {
    return <p className="muted">Every line on this order has already arrived.</p>
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
              onChange={toggleAll}
            />
          </th>
          <th>Item</th>
          <th>Description</th>
          <th>Cost</th>
        </tr>
      </thead>
      <tbody>
        {pending.map((line) => (
          <tr key={line.id}>
            <td className="select-cell">
              <input
                type="checkbox"
                checked={selected.includes(line.id)}
                onChange={() => toggle(line.id)}
              />
            </td>
            <td className="mono">{line.item_code}</td>
            <td>{line.description}</td>
            <td>{money(line.item_cost)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
