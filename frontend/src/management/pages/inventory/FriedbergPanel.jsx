import { useState } from 'react'

import FriedbergLookup from '../receiving/FriedbergLookup'

/**
 * A note's Friedberg number in the item editor: what is attached, and the
 * way to confirm, change or clear it after receiving.
 *
 * Nothing here writes at once. Confirm, Clear and a number chosen in the
 * lookup are held -- shown as not saved yet, with Undo -- and the editor's
 * Save applies them with the rest of the edit (`onHold`, `pending`,
 * `onUndo`), so the editor behaves one way throughout: nothing is kept until
 * Save. A held change that failed carries its reason (`pending.error`).
 */
export default function FriedbergPanel({
  item,
  pending = null,
  onHold,
  onUndo = () => {},
}) {
  const [open, setOpen] = useState(false)
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
        {pending && (
          <p className="notice" role="status">
            {pending.action === 'clear' ? (
              'The number will be cleared when you Save.'
            ) : (
              <>
                <span className="mono">{pending.fr_number}</span> -- {pending.status},
                not saved yet.
              </>
            )}{' '}
            <button type="button" className="link" onClick={onUndo}>
              Undo
            </button>
          </p>
        )}
        {pending?.error && <p className="error">{pending.error}</p>}
        <div className="row">
          {attached && item.friedberg_status !== 'confirmed' && !pending && (
            <button
              type="button"
              onClick={() =>
                onHold({
                  action: 'attach',
                  friedberg_id: item.friedberg_id,
                  status: 'confirmed',
                  fr_number: item.friedberg_number,
                })
              }
            >
              Confirm
            </button>
          )}
          {attached && !pending && (
            <button type="button" onClick={() => onHold({ action: 'clear' })}>
              Clear
            </button>
          )}
          {!open && (
            <button type="button" onClick={() => setOpen(true)}>
              {attached ? 'Look up again' : 'Look up'}
            </button>
          )}
        </div>
        {open && (
          <FriedbergLookup
            itemId={item.id}
            item={item}
            searchNow
            onClose={() => setOpen(false)}
            onChoose={(choice) => {
              onHold({ action: 'attach', ...choice })
              setOpen(false)
            }}
          />
        )}
      </div>
    </div>
  )
}
