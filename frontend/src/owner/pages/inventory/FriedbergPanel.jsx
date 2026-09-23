import { useState } from 'react'

import { api } from '../../api'
import FriedbergLookup from '../receiving/FriedbergLookup'

/**
 * A note's Friedberg number in the item editor: what is attached, and the
 * way to confirm, change or clear it after receiving.
 *
 * Before this the lookup lived only in Receiving, and a received note left
 * that list -- so a number could be attached once and never looked at or
 * corrected again.
 *
 * Self-saving, like the errors and photos panels: each button writes through
 * the Friedberg API at once, independent of the form's own Save. None of it
 * touches the item's `version`, so an edit typed above is not made stale.
 * `onChanged` is the editor's `reloadItem`, which reads the item again
 * without discarding that edit.
 */
export default function FriedbergPanel({ item, onChanged }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function run(action) {
    setBusy(true)
    setError('')
    try {
      await action()
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const attached = item.friedberg_id != null

  return (
    <div className="field friedberg-panel">
      <span>Friedberg</span>
      <div>
        {attached ? (
          <p>
            <span className="mono">{item.friedberg_number}</span> --{' '}
            {item.friedberg_status}
            {item.friedberg_verified ? ', verified' : ', not yet verified'}
          </p>
        ) : (
          <p className="muted">No number attached.</p>
        )}
        <div className="row">
          {attached && item.friedberg_status !== 'confirmed' && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                run(() =>
                  api.attachFriedberg(item.id, {
                    friedberg_id: item.friedberg_id,
                    status: 'confirmed',
                  }),
                )
              }
            >
              Confirm
            </button>
          )}
          {attached && (
            <button
              type="button"
              disabled={busy}
              onClick={() => run(() => api.clearFriedberg(item.id))}
            >
              Clear
            </button>
          )}
          {!open && (
            <button type="button" disabled={busy} onClick={() => setOpen(true)}>
              {attached ? 'Change...' : 'Look up...'}
            </button>
          )}
        </div>
        {error && <p className="error">{error}</p>}
        {open && (
          <FriedbergLookup
            itemId={item.id}
            item={item}
            onClose={() => setOpen(false)}
            onAttached={onChanged}
          />
        )}
      </div>
    </div>
  )
}
