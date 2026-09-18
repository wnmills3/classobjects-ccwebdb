import { useEffect, useRef, useState } from 'react'

import { AccessLabel } from '../AccessLabel'
import { api } from '../api'
import EndOfferConfirm from './EndOfferConfirm'
import ModalDialog from '../ModalDialog'
import { accel, useSaveShortcut } from '../shortcuts'
import { isMoney } from './orders/cents'
import { FORMATS, STATUSES, UNKNOWN, labelFor } from './listing-labels'
import { marginPercent } from './platform-rates'
import { date } from '../../shared/format'

/**
 * The Listings page: every offer the business has out, and the two things
 * that may be done to one.
 *
 * This page never sets a listing's status. `app/offering_writes.py` is the
 * only writer of status and of the claim that keeps an item offered in one
 * place at a time, so ending an offer goes through its own endpoint -- which
 * also resumes the store listing that was paused for it -- rather than
 * through a field on the edit form.
 *
 * Money is shown exactly as it arrives: a decimal string. It is never parsed
 * into a JavaScript number for display or comparison, because a float cannot
 * hold cents exactly. The one calculation here, margin, is done in whole
 * cents by `marginPercent` in `./platform-rates.js`.
 */

/**
 * Alt+letter for each field of the edit window, as every other console edit
 * window has (`docs/system-administration.md`). Gathered in one table so a
 * repeat is visible rather than scattered through the markup, and checked
 * against the rendered dialog by the test beside it.
 *
 * No letter is D, E or F: Chrome and Edge keep those for the address bar and
 * menus on Windows. Save is V, the same letter it is in the item editor, the
 * order editor and the platform form.
 */
const KEYS = {
  price: 'r',
  title: 't',
  description: 'i',
  externalId: 'n',
  save: 'v',
}

/**
 * The edit window for one offer: its price and its wording.
 *
 * Not its platform, its format or its status. Those decide where an item is
 * claimed, and moving an offer from one platform to another is ending it and
 * offering it again -- two writes the claim table is built around, and not
 * something a field assignment can express.
 */
function ListingForm({ listing, onSaved, onClose }) {
  const [form, setForm] = useState({
    price: listing.price,
    title: listing.title,
    description: listing.description,
    external_id: listing.external_id ?? '',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  // Guards save()'s continuation once the request settles: Cancel (and
  // Escape, which ModalDialog routes to onClose) can unmount this form while
  // a save is still in flight, and without the guard a save the user walked
  // away from would still rewrite the row behind the closed dialog.
  //
  // The setup ARMS it; only the cleanup disarms it. The console runs in
  // StrictMode (`owner/main.jsx`), where React runs every effect setup,
  // cleanup, setup on mount: a ref only initialised at `useRef(true)` would
  // be left false by that first cleanup for the rest of the dialog's life,
  // and a save that succeeded would never close it.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // `save` is a function declaration below, hoisted for the whole component
  // scope. Disabled while a save is in flight, so holding Ctrl+S cannot fire
  // a second request behind the first.
  useSaveShortcut(save, !saving)

  async function save() {
    const price = String(form.price).trim()
    if (!isMoney(price)) {
      // Said here rather than left to the API, whose refusal for a blank
      // price is a schema complaint about a Decimal -- true, and no help to
      // someone who cleared the field.
      setError('Price must be an amount like 189.00.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await api.updateListing(listing.id, {
        price,
        title: form.title.trim(),
        // Sent as typed, including empty. `description` is NOT NULL on the
        // row and the API refuses an explicit null for it; an empty string is
        // how the wording is cleared.
        description: form.description,
        external_id: form.external_id.trim() === '' ? null : form.external_id.trim(),
        // The version the form loaded, as a number: `ListingUpdate.version`
        // is an `int` and Pydantic v2 does not coerce "3".
        version: listing.version,
      })
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const label = `Edit ${listing.item_code} on ${listing.venue_name}`

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Price" accessKey={KEYS.price} />
          {/* Text, not number: money crosses the API as a decimal string and
              a number input hands back a float. */}
          <input
            inputMode="decimal"
            value={form.price}
            onChange={set('price')}
            {...accel(KEYS.price)}
          />
        </label>
        <label>
          <AccessLabel text="Title" accessKey={KEYS.title} />
          <input value={form.title} onChange={set('title')} {...accel(KEYS.title)} />
        </label>
        <label>
          <AccessLabel text="Description" accessKey={KEYS.description} />
          <textarea
            rows={3}
            value={form.description}
            onChange={set('description')}
            {...accel(KEYS.description)}
          />
        </label>
        <label>
          <AccessLabel text="Listing number" accessKey={KEYS.externalId} />
          <input
            value={form.external_id}
            onChange={set('external_id')}
            placeholder="the platform's own number"
            {...accel(KEYS.externalId)}
          />
        </label>
      </div>
      <div className="row">
        <button disabled={saving} onClick={save} {...accel(KEYS.save)}>
          <AccessLabel text={saving ? 'Saving...' : 'Save'} accessKey={KEYS.save} />
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

/**
 * The Listings page: what is offered, filtered, with Edit and End per row.
 */
export default function Listings() {
  const [listings, setListings] = useState(null)
  const [venues, setVenues] = useState([])
  // The load failed and there is no table to show. An action's refusal is
  // `refusal` below instead, which leaves the table where it is.
  const [error, setError] = useState('')
  const [refusal, setRefusal] = useState('')
  const [filters, setFilters] = useState({ venue: '', format: '', status: '' })
  const [open, setOpen] = useState(null)
  // The listing End was pressed on, waiting for the question to be answered,
  // and then the one whose request is in flight.
  const [confirming, setConfirming] = useState(null)
  const [ending, setEnding] = useState(null)
  // Bumped to ask for the list again after a write that can change rows this
  // page did not touch.
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    api
      .listSalesVenues()
      .then((rows) => !cancelled && setVenues(rows))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .listListings({
        venue: filters.venue,
        format: filters.format,
        status: filters.status,
      })
      .then((rows) => !cancelled && setListings(rows))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [filters, reloads])

  const filter = (k) => (e) => setFilters({ ...filters, [k]: e.target.value })

  function saved(listing) {
    // A PATCH changes that row and nothing else -- it cannot touch a status
    // or a claim -- so the row is replaced in place rather than refetched.
    setListings((current) => current.map((l) => (l.id === listing.id ? listing : l)))
    setOpen(null)
  }

  async function end(listing) {
    setEnding(listing.id)
    setRefusal('')
    try {
      await api.endListing(listing.id)
      setConfirming(null)
      // Reload rather than patch the one row: ending an offer resumes any
      // store listing paused for it, so a row this page never touched has a
      // new status, and the ended row itself may now fall outside the filter.
      setReloads((n) => n + 1)
    } catch (err) {
      setRefusal(err.message)
      // The question is answered either way: the refusal belongs on the page
      // behind it, where the table it is about still is.
      setConfirming(null)
    } finally {
      setEnding(null)
    }
  }

  if (error) return <p className="error">{error}</p>
  if (listings === null) return <p className="muted">Loading...</p>

  const byId = new Map(listings.map((l) => [l.id, l]))

  /** Why a row is paused, naming the platform when that listing is on screen. */
  function pausedFor(listing) {
    const id = listing.paused_by_listing_id
    if (id === null || id === undefined) return 'paused'
    const cause = byId.get(id)
    // Without the platform when the listing that paused this one is not in
    // the current result: saying less is right, inventing a platform is not.
    return cause
      ? `paused for listing #${id} on ${cause.venue_name}`
      : `paused for listing #${id}`
  }

  return (
    <section>
      <h1>Listings</h1>
      <p className="muted">
        What is offered for sale. An item is offered in one place at a time; a store
        listing set aside for an offer elsewhere resumes when that offer ends.
      </p>
      {refusal && <p className="error">{refusal}</p>}
      <div className="filter-grid">
        <label>
          Platform
          <select value={filters.venue} onChange={filter('venue')}>
            <option value="">All platforms</option>
            {venues.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Format
          <select value={filters.format} onChange={filter('format')}>
            <option value="">Any format</option>
            {FORMATS.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          {/* Blank is the API's own default: active and paused, which is what
              "on offer" means. `all` is the only way to see ended offers. */}
          <select value={filters.status} onChange={filter('status')}>
            <option value="">On offer</option>
            {STATUSES.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
            <option value="all">All, including ended</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>Platform</th>
            <th>Item</th>
            <th>Title</th>
            <th>Price</th>
            <th>Cost</th>
            <th>Margin</th>
            <th>Status</th>
            <th>Listed</th>
            <th>Link</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {listings.map((l) => {
            const margin = marginPercent(l.price, l.cost_basis)
            return (
              <tr key={l.id} className={l.status === 'active' ? '' : 'muted'}>
                <td>{l.venue_name}</td>
                <td>{l.item_code}</td>
                <td>{l.title}</td>
                <td>
                  {l.price} {l.currency}
                </td>
                <td>{l.cost_basis ?? UNKNOWN}</td>
                <td>{margin === '' ? UNKNOWN : `${margin}%`}</td>
                <td>
                  {labelFor(STATUSES, l.status)}
                  {l.status === 'paused' && (
                    <span className="muted"> {pausedFor(l)}</span>
                  )}
                </td>
                <td>{date(l.listed_at)}</td>
                <td>
                  {l.external_url && (
                    <a href={l.external_url} target="_blank" rel="noreferrer">
                      {l.external_id ?? 'Listing'}
                    </a>
                  )}
                </td>
                <td>
                  {/* No Edit on a paused row: it is a store listing set aside
                      for an offer elsewhere, and editing its price while it is
                      out of the shop edits something nobody can see. No Edit
                      or End on an ended one either -- it is history. */}
                  {l.status === 'active' && (
                    <button className="link" onClick={() => setOpen(l)}>
                      Edit
                    </button>
                  )}
                  {/* Ending is permanent: the question comes first, and it
                      names the listing and its platform. */}
                  {l.status !== 'ended' && (
                    <button
                      className="link"
                      disabled={ending !== null}
                      onClick={() => setConfirming(l)}
                    >
                      End
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {listings.length === 0 && (
        <p className="muted">Nothing is offered under those filters.</p>
      )}
      {open !== null && (
        <ListingForm listing={open} onSaved={saved} onClose={() => setOpen(null)} />
      )}
      {confirming !== null && (
        <EndOfferConfirm
          listing={confirming}
          busy={ending !== null}
          onConfirm={() => end(confirming)}
          onCancel={() => setConfirming(null)}
        />
      )}
    </section>
  )
}
