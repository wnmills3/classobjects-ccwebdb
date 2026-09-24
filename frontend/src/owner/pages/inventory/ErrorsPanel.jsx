import { useEffect, useState } from 'react'

import { api } from '../../api'
import { fitsKind, sideFor } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import ForSaleNotice from '../ForSaleNotice'

/**
 * The set as it leaves this panel: no note is null, never the empty string.
 *
 * A row added with the note box left alone already sent null, but a note
 * typed and then cleared sent "". The two mean the same thing to a person and
 * are different rows to the database, so one of them would come back as an
 * empty note that reads as a note. Normalised on the way out only: what is
 * being typed stays exactly as typed.
 */
function withNoEmptyDetails(rows) {
  return rows.map((row) => ({
    ...row,
    details: row.details === '' ? null : row.details,
  }))
}

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
export default function ErrorsPanel({ itemId, kind, value, onChange, saleState }) {
  const controlled = itemId == null
  // null means "not loaded yet"; the controlled mode never reads this and
  // renders through `value` instead.
  const [rows, setRows] = useState(null)
  // Sticky for the editing session: this panel PUTs on every change, and an
  // acknowledgement asked per save would be asked on every keystroke-ish
  // action -- add a row, remove one, blur a note box -- which teaches an
  // operator to tick it blind. Ticked once, it holds for as long as the
  // panel stays mounted.
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState('')
  const [type, setType] = useState('')
  const [details, setDetails] = useState('')
  // `includeRetired`, for the same reason ReferenceSelect asks for it: a type
  // already recorded against an item may since have been retired, and without
  // the retired values here its row would render the raw code instead of its
  // label. The picker below still offers only active ones -- that filtering
  // is ReferenceSelect's, not this list's.
  const vocabulary = useReference('error_type', { includeRetired: true }) ?? []
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
    if (controlled) onChange(withNoEmptyDetails(next))
    else setRows(next)
  }

  /**
   * PUTs the whole set. No-op in the controlled mode -- there is no item yet.
   *
   * No "still mounted?" guard: both continuations only call this panel's own
   * `setError`, and React 19 makes a `setState` on an unmounted component a
   * no-op rather than a warning. The guard that used to be here was armed by
   * `useRef(true)` and disarmed by an unmount cleanup that no setup re-armed,
   * so StrictMode's setup/cleanup/setup on mount -- which is how the console
   * actually runs -- left BOTH branches dead: a PUT that 422'd or timed out
   * showed nothing at all, and the row read as saved. Nothing here needs the
   * guard, so it is gone rather than repaired.
   */
  function save(next) {
    if (controlled) return
    api
      .setItemErrors(itemId, withNoEmptyDetails(next), {
        acknowledgeForSale: acknowledged,
      })
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

  const loading = !controlled && rows === null

  // The heading is inside the panel rather than at each of the three mount
  // points, so every one of them says what this is: without it the panel read
  // as a stray dropdown whose only accessible name was the table's own name,
  // `error_type`. `field` is the shape the item editor's other rows use (see
  // AttributesField): the label in the first grid column, the controls in the
  // second. In Receiving and the new-item form, where no `.edit-form` grid is
  // in play, it lays out as a plain block -- still labelled, which is the
  // part that was missing everywhere.
  return (
    <div className="field errors-panel" data-help="errors">
      <span>Errors</span>
      <div className="error-body">
        <ForSaleNotice
          uses={saleState ?? []}
          checked={acknowledged}
          onChange={setAcknowledged}
          action="Record it anyway"
        />
        {loading && <p className="muted">Loading...</p>}
        {!loading && error && <p className="error">{error}</p>}
        {!loading && (
          <ul className="error-list">
            {list.map((row) => {
              const label = byCode.get(row.error_type)?.label ?? row.error_type
              return (
                <li key={row.error_type}>
                  <span>{label}</span>
                  <input
                    aria-label={`${label} details`}
                    data-help="error_details"
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
        )}
        {!loading && (
          <div className="add-error">
            <ReferenceSelect
              table="error_type"
              value={type}
              onChange={(e) => setType(e.target.value)}
              allowAdd
              labelOnly
              // No top-level `applies_to` field -- see AttributesField in
              // ItemEditForm.jsx for the same shape. An error type added here
              // without the item's side would fit no kind and vanish from
              // this very picker the moment it appeared (`fitsKind`, whose
              // exact inverse `sideFor` is); `error_type` has no
              // `attribute_group` column, so nothing else goes in `extra`.
              addFields={{ applies_to: sideFor(kind) }}
              filter={(entry) =>
                fitsKind(entry, kind) &&
                !list.some((row) => row.error_type === entry.code)
              }
            />
            {/* Labelled, not just placeheld: a placeholder disappears the
                moment anything is typed, and "details" beside a picker whose
                own name is `error_type` said nothing about which error it
                belongs to. */}
            <input
              aria-label="details for the error being added"
              data-help="error_details"
              placeholder="details"
              value={details}
              onChange={(e) => setDetails(e.target.value)}
            />
            <button type="button" onClick={addRow} disabled={!type}>
              Add error
            </button>
          </div>
        )}
      </div>
      <span />
      <span />
    </div>
  )
}
