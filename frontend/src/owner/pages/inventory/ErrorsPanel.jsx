import { useEffect, useState } from 'react'

import { api } from '../../api'
import { fitsKind, isCurrencyKind } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'

/**
 * Mint and printing errors recorded against one item -- a bill is commonly
 * miscut AND misprinted, so this is a set, each entry carrying its own note.
 *
 * Two modes, since Task 6 mounts this in three places. With an `itemId` the
 * panel is self-loading and self-saving: it fetches its own set on mount and
 * PUTs the whole set again whenever a row is added, removed, or edited --
 * there is no separate "Save" step for an item that already has one. With
 * `itemId` null -- a form whose item does not exist yet -- the panel holds no
 * state of its own: `value` is the whole set, and every change is reported
 * through `onChange` for the caller to send once the item is created.
 *
 * A save that fails shows the message but never rolls the edit back: the row
 * a person just typed stays on screen, still editable, rather than vanishing
 * along with the note they wrote.
 */
export default function ErrorsPanel({ itemId, kind, value, onChange }) {
  const controlled = itemId == null
  // null means "not loaded yet"; the controlled mode never reads this and
  // renders through `value` instead.
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [type, setType] = useState('')
  const [details, setDetails] = useState('')
  const vocabulary = useReference('error_type') ?? []
  const byCode = new Map(vocabulary.map((entry) => [entry.code, entry]))

  useEffect(() => {
    if (controlled) return
    let cancelled = false
    api
      .getItemErrors(itemId)
      .then((body) => {
        if (cancelled) return
        setRows(
          (body.errors ?? []).map((e) => ({
            error_type: e.error_type,
            details: e.details ?? '',
          })),
        )
        setError('')
      })
      .catch((err) => {
        // A load failure must not leave the panel stuck showing "Loading...":
        // an empty, editable set with the error message visible is the
        // recoverable state, not a dead end.
        if (cancelled) return
        setError(err.message)
        setRows([])
      })
    return () => {
      cancelled = true
    }
  }, [itemId, controlled])

  const list = controlled ? (value ?? []) : (rows ?? [])

  /** Applies `next` where it lives: the caller's state, or this panel's own. */
  function setList(next) {
    if (controlled) onChange(next)
    else setRows(next)
  }

  /** PUTs the whole set. No-op in the controlled mode -- there is no item yet. */
  function save(next) {
    if (controlled) return
    api
      .setItemErrors(itemId, next)
      .then(() => setError(''))
      .catch((err) => setError(err.message))
  }

  function addRow() {
    const next = [
      ...list,
      { error_type: type, details: details === '' ? null : details },
    ]
    setList(next)
    save(next)
    setType('')
    setDetails('')
  }

  function removeRow(code) {
    const next = list.filter((row) => row.error_type !== code)
    setList(next)
    save(next)
  }

  function editDetails(code, text) {
    // Local (or reported to the controlled caller) on every keystroke, but
    // not saved until the box loses focus -- a PUT of the whole set per
    // character typed would be one request per keystroke.
    setList(
      list.map((row) => (row.error_type === code ? { ...row, details: text } : row)),
    )
  }

  if (!controlled && rows === null) return <p className="muted">Loading...</p>

  return (
    <div className="errors-panel">
      {error && <p className="error">{error}</p>}
      <ul className="error-list">
        {list.map((row) => {
          const label = byCode.get(row.error_type)?.label ?? row.error_type
          return (
            <li key={row.error_type}>
              <span>{label}</span>
              <input
                aria-label={`${label} details`}
                value={row.details ?? ''}
                onChange={(e) => editDetails(row.error_type, e.target.value)}
                onBlur={() => save(list)}
              />
              <button type="button" onClick={() => removeRow(row.error_type)}>
                Remove {label}
              </button>
            </li>
          )
        })}
      </ul>
      <div className="add-error">
        <ReferenceSelect
          table="error_type"
          value={type}
          onChange={(e) => setType(e.target.value)}
          allowAdd
          labelOnly
          // No top-level `applies_to` field -- see AttributesField in
          // ItemEditForm.jsx for the same shape. An error type added here
          // without the item's side would fit no kind and vanish from this
          // very picker the moment it appeared (`fitsKind`); `error_type` has
          // no `attribute_group` column, so nothing else goes in `extra`.
          addFields={{ applies_to: isCurrencyKind(kind) ? 'currency' : 'coin' }}
          filter={(entry) =>
            fitsKind(entry, kind) && !list.some((row) => row.error_type === entry.code)
          }
        />
        <input
          placeholder="details"
          value={details}
          onChange={(e) => setDetails(e.target.value)}
        />
        <button type="button" onClick={addRow} disabled={!type}>
          Add error
        </button>
      </div>
    </div>
  )
}
