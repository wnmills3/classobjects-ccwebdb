import { useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import ItemPicker from '../ItemPicker'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import { useRequest } from '../../../shared/useRequest'

/**
 * Every photograph filed against this item, and the place a new one is
 * attached -- from a file, or from a web address the server fetches,
 * converts and names for its place (`CC-000412_02.jpg`). A new photograph
 * says what it shows. Nothing here writes: a new photograph (`pending`), and
 * a role, primary, removal or move to another item for one already filed
 * (`edits`, keyed by link id), are held and shown as not saved yet until the
 * editor's Save applies them, like every other change in the editor.
 *
 * Until now `api.uploadImage` had exactly one caller: the receiving screen,
 * so a photograph could only ever be attached at the moment an item
 * arrived. The owner photographs at leisure, often well after receiving --
 * this panel is what gives an item a second, later chance to gain one, in
 * the place the owner already returns to finish a record: the item editor.
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
 * names it for its place there. Before this, a photograph on the wrong item
 * was removed here and filed again from /management/photos, keeping the old
 * item's name.
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
  const links = photos.error
    ? []
    : photos.data
      ? [...photos.data].sort((a, b) => a.sort_order - b.sort_order)
      : null
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
  // What a photograph added next is held as: the obverse, then the reverse,
  // whichever the item lacks; once it has both, nothing, and its own picker
  // in the held list asks. The role is chosen beside the photograph it
  // belongs to, never beside the add controls -- a picker there sat under
  // the photograph just added and read as its label, so a person set the
  // first photograph's side on the second (owner's report, CC-008085).
  const [address, setAddress] = useState('')
  // The photograph whose Move picker is open, and what it last refused.
  const [moving, setMoving] = useState(null)
  const [moveError, setMoveError] = useState('')
  // Whether a drag carrying files is currently over the drop target, and
  // what the last file dropped or pasted there was refused for -- a chosen
  // file never fails this check (the file box already filters by
  // `accept="image/*"`), so this only ever fires from a drop or a paste.
  const [dragActive, setDragActive] = useState(false)
  const [pickError, setPickError] = useState('')
  // A nesting count, not a flag: entering the drop target's own children
  // (the label, the input, the help text) fires a dragenter on the child and
  // a dragleave on the target itself, since only the topmost element under
  // the pointer counts as "current" -- a flag would blink the hint off for
  // every child crossed. Active while the count is above zero; a drop or a
  // cancelled drag (see the window listener below) resets it to zero rather
  // than trusting the count to unwind on its own.
  const dragDepth = useRef(0)
  // A held photograph counts: two added at once are an obverse and a reverse.
  const held = new Set([
    ...shown.filter((row) => !leaving(row.edit)).map((row) => row.role),
    ...pending.map((entry) => entry.role),
  ])
  const nextSide = ['obverse', 'reverse'].find((code) => !held.has(code)) ?? ''
  const activeRoles = vocabulary.filter((entry) => entry.is_active !== false)

  function roleLabel(code) {
    if (!code) return 'Unfiled role'
    return byCode.get(code)?.label ?? code
  }

  /**
   * What choosing a file, dropping one, and pasting one all funnel into --
   * the input, `handleDrop` and `handlePaste` below call nothing else. Each
   * file is held exactly as `upload` always held its one, the obverse then
   * the reverse then unlabelled, counting whatever a batch has already
   * claimed as it goes. A file that is not a photograph refuses the whole
   * drop or paste with a message naming it, rather than silently skipping
   * it -- the file box itself never reaches this branch, since
   * `accept="image/*"` already keeps a non-image out of `e.target.files`.
   */
  function addFiles(fileList) {
    const files = Array.from(fileList ?? []).filter(Boolean)
    if (files.length === 0) return
    const notImages = files.filter((file) => !file.type.startsWith('image/'))
    if (notImages.length > 0) {
      const names = notImages.map((file) => file.name).join(', ')
      setPickError(
        `${names} ${notImages.length > 1 ? 'are not images' : 'is not an image'} -- ` +
          'only a photograph can be added here.',
      )
      return
    }
    setPickError('')
    const claimed = new Set(held)
    for (const file of files) {
      const role = ['obverse', 'reverse'].find((code) => !claimed.has(code)) ?? ''
      if (role) claimed.add(role)
      // Held, not sent: the editor's Save files it with everything else.
      onAdd({ kind: 'file', file, role })
    }
  }

  function upload(e) {
    addFiles(e.target.files)
    e.target.value = ''
  }

  function handleDragOver(e) {
    // Without this the browser's own default takes over: dropping an image
    // on the page opens it in the tab instead of reaching this panel.
    e.preventDefault()
  }

  function handleDragEnter(e) {
    e.preventDefault()
    dragDepth.current += 1
    setDragActive(true)
  }

  function handleDragLeave(e) {
    e.preventDefault()
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragActive(false)
  }

  function handleDrop(e) {
    e.preventDefault()
    dragDepth.current = 0
    setDragActive(false)
    addFiles(e.dataTransfer?.files)
  }

  // A drag cancelled outright -- Escape, or a drop outside the browser
  // window -- can leave this panel's own dragleave never firing, since the
  // pointer never crosses the target's boundary again to trigger one. Both
  // `dragend` (fired on the source once the operation ends) and `drop`
  // (fired wherever it actually lands) are caught at the window regardless
  // of where that is, as a backstop for the per-target handlers above.
  useEffect(() => {
    function reset() {
      dragDepth.current = 0
      setDragActive(false)
    }
    window.addEventListener('dragend', reset)
    window.addEventListener('drop', reset)
    return () => {
      window.removeEventListener('dragend', reset)
      window.removeEventListener('drop', reset)
    }
  }, [])

  function handlePaste(e) {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles = Array.from(items)
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter(Boolean)
    // Nothing to add: leave the event alone so plain text still pastes
    // normally wherever this was actually aimed.
    if (imageFiles.length === 0) return
    e.preventDefault()
    addFiles(imageFiles)
  }

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
                <img src={row.thumbnail_url} alt={label} />
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
                      allowAdd={false}
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
            className={`photo-drop${dragActive ? ' photo-drop-active' : ''}`}
            tabIndex={0}
            aria-label="Add an image: drag one here, or paste one with Ctrl+V"
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onPaste={handlePaste}
          >
            <label>
              Photo
              <input type="file" accept="image/*" onChange={upload} />
            </label>
            <p className="muted">
              Choose a file, drag one here, or paste an image (Ctrl+V).
            </p>
            {pickError && <p className="error">{pickError}</p>}
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
