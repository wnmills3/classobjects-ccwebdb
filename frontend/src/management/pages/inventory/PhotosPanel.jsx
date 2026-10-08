import { useState } from 'react'

import { api } from '../../api'
import EnlargeableImage from '../../EnlargeableImage'
import ItemPicker from '../ItemPicker'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import { useRequest } from '../../../shared/useRequest'
import { useImageDrop } from '../../useImageDrop'

/**
 * Every photograph filed against this item, and the place a new one is
 * attached -- from a file, or from a web address the server fetches,
 * converts and names for its place (`CC-000412_02.jpg`). A new photograph
 * says what it shows. Nothing here writes: a new photograph (`pending`), and
 * a role, primary, removal or move to another item for one already filed
 * (`edits`, keyed by link id), are held and shown as not saved yet until the
 * editor's Save applies them, like every other change in the editor.
 *
 * The owner photographs at leisure, often well after receiving, so an item
 * gains a photograph here as well as when it arrives: in the place the owner
 * already returns to finish a record, the item editor.
 *
 * The filed photographs are read from the server on mount and again once
 * the editor's Save has applied what was held (`reloadKey`); the held
 * changes are laid over them. Choosing a photograph's saved role again, or
 * making the saved primary primary again, drops the held change rather than
 * holding a no-op.
 *
 * A held "Remove" becomes `api.detachImage` on Save, never a delete of the
 * underlying photograph -- see the module doc on `routers/image_links.py`.
 * Unfiling a photograph is a filing correction; destroying the bytes is a
 * different, much rarer action this panel does not offer.
 *
 * A held "Move" files the photograph on another item, found by its code,
 * in one step -- the server places it after that item's photographs and
 * names it for its place there.
 *
 * No for-sale notice of its own: nothing here writes, and the editor's one
 * acknowledgement covers everything its Save applies.
 */
export default function PhotosPanel({
  itemId,
  // Photographs added here and held for the editor's Save, and the
  // editor's handlers for adding one, re-labelling one and discarding one.
  pending = [],
  onAdd = () => {},
  onRoleChange = () => {},
  onDiscard = () => {},
  // Changes held for filed photographs, {link_id: {image_role?,
  // is_primary?, remove?, move_to?: {id, item_code}, error?}}, and the
  // editor's setter for the whole map.
  edits = {},
  onEditsChange = () => {},
  // Bumped by the editor once it has applied what was held, to read the
  // photographs again.
  reloadKey = 0,
}) {
  // Read again after every Save -- the server, not this panel's own guess,
  // decides sort order, primacy and which photograph a link now points at.
  const photos = useRequest(`${itemId}:${reloadKey}`, () => api.listItemImages(itemId))
  // A load failure must not leave the panel stuck on "Loading...": an empty,
  // still-usable panel with the error shown is recoverable, a dead end is not.
  let links = null
  if (photos.error) links = []
  else if (photos.data) {
    links = [...photos.data].sort((a, b) => a.sort_order - b.sort_order)
  }
  const error = photos.error
  // What each filed photograph will be once Save applies what is held.
  // A photograph removed or moved away is leaving this item.
  const leaving = (edit) => Boolean(edit.remove || edit.move_to)
  const heldPrimary = Object.keys(edits).find(
    (id) => edits[id].is_primary && !leaving(edits[id]),
  )
  const shown = (links ?? []).map((row) => {
    const edit = edits[row.link_id] ?? {}
    return {
      ...row,
      edit,
      role: 'image_role' in edit ? edit.image_role : row.image_role,
      primary:
        heldPrimary === undefined
          ? row.is_primary
          : String(row.link_id) === heldPrimary,
    }
  })
  // Retired roles included, the same reason `ErrorsPanel` asks for them: a
  // role recorded on a link may since have been retired, and without it the
  // row would fall back to showing the raw code instead of its label.
  const vocabulary = useReference('image_role', { includeRetired: true }) ?? []
  const byCode = new Map(vocabulary.map((entry) => [entry.code, entry]))
  const [address, setAddress] = useState('')
  // The photograph whose Move picker is open, and what it last refused.
  const [moving, setMoving] = useState(null)
  const [moveError, setMoveError] = useState('')
  // The roles already taken, by the filed photographs that are staying and
  // by the held ones. A held photograph counts: two added at once are an
  // obverse and a reverse.
  const held = new Set([
    ...shown.filter((row) => !leaving(row.edit)).map((row) => row.role),
    ...pending.map((entry) => entry.role),
  ])
  // What a photograph added next is held as: the obverse, then the reverse,
  // whichever the item lacks; once it has both, nothing, and its own picker
  // in the held list asks. The role is chosen beside the photograph it
  // belongs to, never beside the add controls, where a picker reads as the
  // label of the photograph above it.
  const nextSide = ['obverse', 'reverse'].find((code) => !held.has(code)) ?? ''
  const activeRoles = vocabulary.filter((entry) => entry.is_active !== false)

  function roleLabel(code) {
    if (!code) return 'Unfiled role'
    return byCode.get(code)?.label ?? code
  }

  /**
   * What choosing a file, dropping one, and pasting one all funnel into
   * (`useImageDrop`). Each file is held as the obverse, then the reverse,
   * then unlabelled, counting whatever a batch has already claimed as it
   * goes.
   */
  function addImages(files) {
    const claimed = new Set(held)
    for (const file of files) {
      const role = ['obverse', 'reverse'].find((code) => !claimed.has(code)) ?? ''
      if (role) claimed.add(role)
      // Held, not sent: the editor's Save files it with everything else.
      onAdd({ kind: 'file', file, role })
    }
  }

  const drop = useImageDrop({ onImages: addImages })

  function addFromAddress() {
    onAdd({ kind: 'url', url: address.trim(), role: nextSide })
    setAddress('')
  }

  /**
   * Hold `change` for `row`, keeping the map free of no-ops: a field that
   * matches the saved photograph is dropped, and so is an entry left empty.
   * A held failure is cleared by any new choice for that photograph.
   */
  function hold(row, change) {
    const next = { ...edits }
    const entry = { ...next[row.link_id], ...change }
    delete entry.error
    if ('image_role' in entry && entry.image_role === row.image_role) {
      delete entry.image_role
    }
    if (change.is_primary) {
      // One primary: a primary held for another photograph gives way.
      for (const id of Object.keys(next)) {
        if (!next[id].is_primary) continue
        const rest = { ...next[id] }
        delete rest.is_primary
        if (Object.keys(rest).length > 0) next[id] = rest
        else delete next[id]
      }
      if (row.is_primary) delete entry.is_primary
    }
    if (Object.keys(entry).length > 0) next[row.link_id] = entry
    else delete next[row.link_id]
    onEditsChange(next)
  }

  function moveTo(row, item) {
    // null: the code was edited after a pick; nothing chosen yet.
    if (!item) return
    if (item.id === itemId) {
      setMoveError(`That photograph is already on this item (${item.item_code}).`)
      return
    }
    onEditsChange({
      ...edits,
      [row.link_id]: { move_to: { id: item.id, item_code: item.item_code } },
    })
    setMoving(null)
    setMoveError('')
  }

  function undo(row) {
    const next = { ...edits }
    delete next[row.link_id]
    onEditsChange(next)
  }

  const loading = links === null

  return (
    <div className="photos-panel" data-help="photos">
      <h3>Photographs</h3>
      {loading && <p className="muted">Loading...</p>}
      {!loading && error && <p className="error">{error}</p>}
      {pending.length > 0 && (
        <ul className="photo-pending">
          {pending.map((entry) => {
            const name = entry.kind === 'file' ? entry.file.name : entry.url
            return (
              <li key={entry.key}>
                <span>{name} -- not saved yet</span>
                <select
                  aria-label={`What ${name} shows`}
                  value={entry.role}
                  onChange={(e) => onRoleChange(entry.key, e.target.value)}
                >
                  <option value="">-- what it shows --</option>
                  {activeRoles.map((role) => (
                    <option key={role.code} value={role.code}>
                      {role.label}
                    </option>
                  ))}
                </select>
                {!entry.role && (
                  <span className="notice" role="status">
                    Choose what it shows before saving.
                  </span>
                )}
                {entry.error && <span className="error"> {entry.error}</span>}
                <button
                  type="button"
                  className="link"
                  onClick={() => onDiscard(entry.key)}
                >
                  Discard
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {!loading && links.length === 0 && (
        <p className="muted">No photographs filed yet.</p>
      )}
      {!loading && links.length > 0 && (
        <ul className="photo-list">
          {shown.map((row) => {
            const label = roleLabel(row.role)
            const { edit } = row
            const changed = Object.keys(edit).some((key) => key !== 'error')
            return (
              <li key={row.link_id}>
                <EnlargeableImage
                  src={row.thumbnail_url}
                  largeSrc={row.image_url}
                  sourceUrl={row.source_url}
                  alt={label}
                />
                {row.primary && !leaving(edit) && (
                  <span className="primary-marker">Primary</span>
                )}
                {edit.remove && (
                  <span className="notice">{label} -- removed when you Save.</span>
                )}
                {edit.move_to && (
                  <span className="notice">
                    {label} -- moves to {edit.move_to.item_code} when you Save.
                  </span>
                )}
                {!leaving(edit) && (
                  <>
                    <ReferenceSelect
                      table="image_role"
                      value={row.role ?? ''}
                      onChange={(e) =>
                        hold(row, { image_role: e.target.value || null })
                      }
                      // A new kind of photograph is added here by its name,
                      // as an attribute is. The value is
                      // created at once; this photograph's use of it is held
                      // for Save like any other role change.
                      allowAdd
                      labelOnly
                    />
                    {!row.primary && (
                      <button
                        type="button"
                        onClick={() => hold(row, { is_primary: true })}
                      >
                        Make primary (photo {row.image_id})
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() =>
                        onEditsChange({ ...edits, [row.link_id]: { remove: true } })
                      }
                    >
                      Remove (photo {row.image_id})
                    </button>
                    {moving !== row.link_id && (
                      <button
                        type="button"
                        onClick={() => {
                          setMoving(row.link_id)
                          setMoveError('')
                        }}
                      >
                        Move (photo {row.image_id})
                      </button>
                    )}
                  </>
                )}
                {moving === row.link_id && !leaving(edit) && (
                  <div className="photo-move">
                    <span>Move to the item with code:</span>
                    <ItemPicker onPick={(item) => moveTo(row, item)} />
                    {moveError && <p className="error">{moveError}</p>}
                    <button
                      type="button"
                      className="link"
                      onClick={() => setMoving(null)}
                    >
                      Cancel the move
                    </button>
                  </div>
                )}
                {changed && !leaving(edit) && (
                  <span className="notice">Not saved yet.</span>
                )}
                {changed && (
                  <button type="button" className="link" onClick={() => undo(row)}>
                    Undo (photo {row.image_id})
                  </button>
                )}
                {edit.error && <span className="error"> {edit.error}</span>}
              </li>
            )
          })}
        </ul>
      )}
      {!loading && (
        <div className="photo-add">
          <div
            className={`photo-drop${drop.dragActive ? ' photo-drop-active' : ''}`}
            tabIndex={0}
            aria-label="Add an image: drag one here, or paste one with Ctrl+V"
            {...drop.target}
          >
            <label>
              Photo{/* */}
              <input type="file" accept="image/*" onChange={drop.onFileInput} />
            </label>
            <p className="muted">
              Choose a file, drag one here, or paste an image (Ctrl+V).
            </p>
            {drop.pickError && <p className="error">{drop.pickError}</p>}
          </div>
          <label>
            Photo web address{/* */}
            <input
              type="url"
              placeholder="https://"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </label>
          <button type="button" onClick={addFromAddress} disabled={!address.trim()}>
            Add from web address
          </button>
        </div>
      )}
    </div>
  )
}
