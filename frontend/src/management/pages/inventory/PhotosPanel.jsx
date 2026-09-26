import { useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'
import ForSaleNotice from '../ForSaleNotice'
import { useRequest } from '../../../shared/useRequest'

/**
 * Every photograph filed against this item, and the place a new one is
 * attached.
 *
 * Until now `api.uploadImage` had exactly one caller: the receiving screen,
 * so a photograph could only ever be attached at the moment an item
 * arrived. The owner photographs at leisure, often well after receiving --
 * this panel is what gives an item a second, later chance to gain one, in
 * the place the owner already returns to finish a record: the item editor.
 *
 * Read from the server on mount and again after every write, never from the
 * editor's draft -- the same choice `OffersPanel` makes and for the same
 * reason. A photograph filed, re-roled, promoted or removed here changes
 * what the server knows about the item regardless of whether the form's own
 * Save is ever pressed, and nothing else on this page needs the item's
 * photographs to render itself.
 *
 * "Remove" calls `api.detachImage`, never a delete of the underlying
 * photograph -- see the module doc on `routers/image_links.py`. Unfiling a
 * photograph is a filing correction; destroying the bytes is a different,
 * much rarer action this panel does not offer.
 *
 * `acknowledged` is sticky for the panel's life, the same decision
 * `ErrorsPanel` makes and for the same reason: this panel writes on every
 * action -- upload, re-role, make primary, remove -- rather than behind a
 * Save button, so asking again on each one would teach the operator to tick
 * the box without reading it.
 */
export default function PhotosPanel({ itemId, saleState }) {
  // Read again after every write -- the server, not this panel's own guess,
  // decides sort order, primacy and which photograph a link now points at.
  const photos = useRequest(itemId, () => api.listItemImages(itemId))
  // A load failure must not leave the panel stuck on "Loading...": an empty,
  // still-usable panel with the error shown is recoverable, a dead end is not.
  const links = photos.error
    ? []
    : photos.data
      ? [...photos.data].sort((a, b) => a.sort_order - b.sort_order)
      : null
  const [actionError, setError] = useState('')
  const error = actionError || photos.error
  const [acknowledged, setAcknowledged] = useState(false)
  // Retired roles included, the same reason `ErrorsPanel` asks for them: a
  // role recorded on a link may since have been retired, and without it the
  // row would fall back to showing the raw code instead of its label.
  const vocabulary = useReference('image_role', { includeRetired: true }) ?? []
  const byCode = new Map(vocabulary.map((entry) => [entry.code, entry]))

  const reload = photos.reload

  function roleLabel(code) {
    if (!code) return 'Unfiled role'
    return byCode.get(code)?.label ?? code
  }

  function upload(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    api
      .uploadImage(itemId, file, { acknowledgeForSale: acknowledged })
      .then(() => {
        setError('')
        reload()
      })
      .catch((err) => setError(err.message))
  }

  function setRole(row, code) {
    api
      .updateImageLink(row.link_id, {
        imageRole: code || null,
        acknowledgeForSale: acknowledged,
      })
      .then(() => {
        setError('')
        reload()
      })
      .catch((err) => setError(err.message))
  }

  function makePrimary(row) {
    api
      .updateImageLink(row.link_id, {
        isPrimary: true,
        acknowledgeForSale: acknowledged,
      })
      .then(() => {
        setError('')
        reload()
      })
      .catch((err) => setError(err.message))
  }

  function remove(row) {
    // The link only -- see the docstring above. `api.detachImage`, never a
    // delete of the photograph itself.
    api
      .detachImage(row.link_id, { acknowledgeForSale: acknowledged })
      .then(() => {
        setError('')
        reload()
      })
      .catch((err) => setError(err.message))
  }

  const loading = links === null

  return (
    <div className="photos-panel" data-help="photos">
      <h3>Photographs</h3>
      <ForSaleNotice
        uses={saleState ?? []}
        checked={acknowledged}
        onChange={setAcknowledged}
        action="Change the photographs anyway"
      />
      {loading && <p className="muted">Loading...</p>}
      {!loading && error && <p className="error">{error}</p>}
      {!loading && links.length === 0 && (
        <p className="muted">No photographs filed yet.</p>
      )}
      {!loading && links.length > 0 && (
        <ul className="photo-list">
          {links.map((row) => {
            const label = roleLabel(row.image_role)
            return (
              <li key={row.link_id}>
                <img src={row.thumbnail_url} alt={label} />
                {row.is_primary && <span className="primary-marker">Primary</span>}
                <ReferenceSelect
                  table="image_role"
                  value={row.image_role ?? ''}
                  onChange={(e) => setRole(row, e.target.value)}
                  allowAdd={false}
                />
                {!row.is_primary && (
                  <button type="button" onClick={() => makePrimary(row)}>
                    Make primary (photo {row.image_id})
                  </button>
                )}
                <button type="button" onClick={() => remove(row)}>
                  Remove (photo {row.image_id})
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {!loading && (
        <label>
          Photo
          <input type="file" accept="image/*" onChange={upload} />
        </label>
      )}
    </div>
  )
}
