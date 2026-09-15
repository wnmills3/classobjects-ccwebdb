import { useEffect, useState } from 'react'

import { api } from '../../api'

const title = (row) => row.listing_title ?? `listing ${row.listing_id}`

/** One history row as a phrase. Money values arrive as plain decimals. */
export function describeChange(row) {
  const from = row.from_value
  const to = row.to_value
  switch (row.change) {
    case 'placed':
      return `placed by ${to}`
    case 'line_added':
      return `added ${title(row)}: ${to}`
    case 'line_removed':
      return `removed ${title(row)} (was ${from})`
    case 'quantity':
      return `${title(row)} quantity ${from} -> ${to}`
    case 'unit_price':
      return `${title(row)} price $${from} -> $${to}`
    case 'customer':
      return `customer ${from} -> ${to}`
    case 'notes':
      return 'notes changed'
    case 'status':
      return `status ${from} -> ${to}`
    case 'total':
      return `total $${from} -> $${to}`
    default:
      return row.change
  }
}

/**
 * An order's history, one entry per save. Rows arrive newest first; a save's
 * rows share a time and an account, so consecutive rows sharing both are one
 * entry, read oldest change first.
 */
export default function OrderHistory({ order, onClose }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api
      .listOrderChanges(order.id)
      .then((body) => !cancelled && setRows(body))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [order.id])

  const groups = []
  for (const row of rows ?? []) {
    const last = groups[groups.length - 1]
    if (last && last.at === row.changed_at && last.by === row.changed_by_email) {
      last.rows.unshift(row)
    } else {
      groups.push({ at: row.changed_at, by: row.changed_by_email, rows: [row] })
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>History of order #{order.id}</h2>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {rows && groups.length === 0 && <p className="muted">No recorded changes.</p>}
      <ul>
        {groups.map((group) => (
          <li key={`${group.at}-${group.by}`}>
            <strong>{new Date(group.at).toLocaleString()}</strong>{' '}
            {group.by ?? 'unknown account'}: {group.rows.map(describeChange).join('; ')}
          </li>
        ))}
      </ul>
    </div>
  )
}
