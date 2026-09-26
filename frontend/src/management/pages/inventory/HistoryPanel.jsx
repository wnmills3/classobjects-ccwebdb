import { useEffect, useState } from 'react'

import { dateTime } from '../../../shared/format'
import { api } from '../../api'
import { fieldName, shown } from './fieldMerge'

/** Rows shown before "Show all": the recent past is what is usually asked. */
const FIRST_ROWS = 20

/** What an entry changed, as the list names it. */
function subject(event) {
  if (event.kind === 'status') return 'Status'
  if (event.kind === 'location') return 'Location'
  return fieldName(event.field)
}

/**
 * Everything logged about the item, newest first: field edits, status moves
 * and location moves (`GET /api/inventory/{id}/history`).
 *
 * Read-only, like the sale history beside it. `version` is the item's
 * version: a save, or a change read in from elsewhere, moves it, and the
 * list is read again so the change just made is on it.
 *
 * An edit made by a machine pass, or before the change log existed, has no
 * entry; the note under the list says so, because an empty history is
 * otherwise read as "never changed".
 */
export default function HistoryPanel({ itemId, version }) {
  const [events, setEvents] = useState(null)
  const [error, setError] = useState('')
  const [all, setAll] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .getItemHistory(itemId)
      .then((rows) => {
        if (cancelled) return
        setEvents(rows)
        setError('')
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [itemId, version])

  const rows = events === null ? [] : all ? events : events.slice(0, FIRST_ROWS)

  return (
    <div className="history-panel">
      <h3>History</h3>
      {error && <p className="error">{error}</p>}
      {events === null && !error && <p className="muted">Loading...</p>}
      {events !== null && events.length === 0 && (
        <p className="muted">Nothing logged yet.</p>
      )}
      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What</th>
              <th>From</th>
              <th>To</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={`${e.at}-${e.kind}-${e.field}-${i}`}>
                <td>{dateTime(e.at)}</td>
                <td>{e.by ?? 'unknown'}</td>
                <td>{subject(e)}</td>
                <td>{shown(e.old_value)}</td>
                <td>{shown(e.new_value)}</td>
                <td>
                  {[e.note, e.arrived_on && `arrived ${e.arrived_on}`]
                    .filter(Boolean)
                    .join('; ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {events !== null && events.length > FIRST_ROWS && (
        <button className="link" onClick={() => setAll(!all)}>
          {all ? 'Show recent only' : `Show all ${events.length}`}
        </button>
      )}
      {events !== null && (
        <p className="muted">
          Field edits are logged since 23 Sep 2026; a change made before then, or by
          most clean-up passes, has no entry.
        </p>
      )}
    </div>
  )
}
