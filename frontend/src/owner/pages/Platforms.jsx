import { useEffect, useRef, useState } from 'react'

import { AccessLabel } from '../AccessLabel'
import { api } from '../api'
import ModalDialog from '../ModalDialog'
import { accel, useSaveShortcut } from '../shortcuts'
import { fractionToPercent, percentToFraction } from './platform-rates'
import { useReference } from '../../shared/reference-context'

/**
 * The platforms the business sells through.
 *
 * The web store is created with the database and is the one platform whose
 * kind is fixed and which cannot be retired: the shop's checkout is defined
 * by it. Every other platform -- eBay, Whatnot, an auction house -- is added
 * here and may be linked to the purchase source of the same name, so a
 * partner exists once whether items are bought or sold there.
 *
 * Default fees only estimate a sale's net when pricing; a sale records what
 * was actually charged. Rates are typed as percentages and sent as fractions
 * (13.25 -> "0.1325"), because that is how the API and the database hold them.
 */

const SAMPLE_ID = '123456789'

/**
 * Alt+letter for each field of the platform form, as every other console edit
 * window has (`docs/system-administration.md`). Gathered in one table so a
 * repeat is visible rather than scattered through the markup, and checked by
 * the test below it.
 *
 * No letter is D, E or F: Chrome and Edge keep those for the address bar and
 * menus on Windows. Save is V, the same letter it is in the item editor and
 * the order editor. Each letter appears in its own label, so `AccessLabel`
 * has something to underline. Cancel has none -- Escape closes the dialog,
 * which `ModalDialog` already handles.
 */
const KEYS = {
  name: 'n',
  code: 'c',
  kind: 'k',
  vendor: 'p',
  account: 'a',
  template: 'l',
  commission: 'm',
  processing: 'r',
  processingFixed: 's',
  listingFee: 'g',
  termsAsOf: 'o',
  notes: 't',
  retired: 'i',
  save: 'v',
}

/** Whether a fee was given at all. A fee of zero was. */
const given = (fee) => fee !== null && fee !== undefined && fee !== ''

function feeSummary(v) {
  // Presence, not truthiness. "This platform charges nothing" is a fact and
  // is not the same as "nobody has looked its fees up yet", which shows an
  // empty cell. A truthiness test conflates the two for any zero JavaScript
  // calls falsy -- today the API sends Decimal as a string, so "0.0000"
  // survives it by luck, but a JSON 0 would disappear without a trace.
  const parts = []
  if (given(v.commission_rate)) parts.push(`${fractionToPercent(v.commission_rate)}%`)
  if (given(v.processing_rate)) parts.push(`${fractionToPercent(v.processing_rate)}%`)
  if (given(v.processing_fixed)) parts.push(`$${v.processing_fixed}`)
  if (given(v.listing_fee)) parts.push(`$${v.listing_fee} per listing`)
  return parts.join(' + ')
}

const BLANK = {
  code: '',
  name: '',
  kind: '',
  vendor_id: '',
  account_handle: '',
  listing_url_template: '',
  commission_pct: '',
  processing_pct: '',
  processing_fixed: '',
  listing_fee: '',
  terms_as_of: '',
  notes: '',
  is_active: true,
}

function toForm(v) {
  return {
    code: v.code,
    name: v.name,
    kind: v.kind,
    vendor_id: v.vendor_id ?? '',
    account_handle: v.account_handle ?? '',
    listing_url_template: v.listing_url_template ?? '',
    commission_pct: fractionToPercent(v.commission_rate),
    processing_pct: fractionToPercent(v.processing_rate),
    processing_fixed: v.processing_fixed ?? '',
    listing_fee: v.listing_fee ?? '',
    terms_as_of: v.terms_as_of ?? '',
    notes: v.notes ?? '',
    is_active: v.is_active,
  }
}

const orNull = (text) => (String(text).trim() === '' ? null : String(text).trim())

function toPayload(form) {
  return {
    name: form.name.trim(),
    kind: form.kind,
    vendor_id: form.vendor_id === '' ? null : Number(form.vendor_id),
    account_handle: orNull(form.account_handle),
    listing_url_template: orNull(form.listing_url_template),
    commission_rate: percentToFraction(form.commission_pct),
    processing_rate: percentToFraction(form.processing_pct),
    processing_fixed: orNull(form.processing_fixed),
    listing_fee: orNull(form.listing_fee),
    terms_as_of: orNull(form.terms_as_of),
    notes: orNull(form.notes),
    is_active: form.is_active,
  }
}

/**
 * The add/edit form for one platform, opened as a modal dialog.
 *
 * `venue` is `null` while adding a new platform and the record being edited
 * otherwise. The web store's kind and active status cannot be changed here,
 * so those fields are hidden for it and dropped from what gets sent.
 */
function PlatformForm({ venue, venues, vendors, onSaved, onClose }) {
  const adding = venue === null
  const [form, setForm] = useState(adding ? BLANK : toForm(venue))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const kinds = useReference('sales_venue_kind') ?? []
  const isStore = !adding && venue.is_own_store
  const set = (k) => (e) =>
    setForm({
      ...form,
      [k]: e.target.type === 'checkbox' ? !e.target.checked : e.target.value,
    })

  // A purchase source may be linked to one platform: offer the free ones
  // and this platform's own.
  const taken = new Set(
    venues.filter((v) => v.vendor_id && v.code !== venue?.code).map((v) => v.vendor_id),
  )
  const sources = vendors.filter((v) => !taken.has(v.id))

  // Guards save()'s continuation once the request settles. Cancel (and
  // Escape, which ModalDialog also routes to onClose) can unmount this form
  // while a save is still in flight; without this a request the user just
  // cancelled would still land the instant it resolves -- onSaved would
  // mutate the parent's list behind the closed dialog. Mirrors the
  // `cancelled` flag Platforms' own effect uses for the same reason.
  const mounted = useRef(true)
  useEffect(
    () => () => {
      mounted.current = false
    },
    [],
  )

  // `save` is a function declaration below, hoisted for the whole component
  // scope, so naming it here is safe. Disabled while a save is in flight, so
  // holding Ctrl+S cannot fire a second request behind the first.
  useSaveShortcut(save, !saving)

  async function save() {
    setSaving(true)
    setError('')
    try {
      const payload = toPayload(form)
      let saved
      if (adding) {
        // SalesVenueCreate has no is_active field and forbids extras; a new
        // platform is active by default.
        delete payload.is_active
        saved = await api.createSalesVenue({ ...payload, code: form.code.trim() })
      } else {
        if (isStore) {
          delete payload.kind
          delete payload.is_active
        }
        saved = await api.updateSalesVenue(venue.code, {
          ...payload,
          version: venue.version,
        })
      }
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const label = adding ? 'Add platform' : `Edit ${venue.name}`
  const sample = form.listing_url_template.includes('{external_id}')
    ? form.listing_url_template.replace('{external_id}', SAMPLE_ID)
    : ''

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Name" accessKey={KEYS.name} />
          <input value={form.name} onChange={set('name')} {...accel(KEYS.name)} />
        </label>
        {adding && (
          <label>
            <AccessLabel text="Code" accessKey={KEYS.code} />
            <input
              value={form.code}
              onChange={set('code')}
              placeholder="lower-case, e.g. ebay"
              {...accel(KEYS.code)}
            />
          </label>
        )}
        {!isStore && (
          <label>
            <AccessLabel text="Kind" accessKey={KEYS.kind} />
            <select value={form.kind} onChange={set('kind')} {...accel(KEYS.kind)}>
              <option value="">(choose)</option>
              {kinds
                .filter((k) => k.code !== 'own_store')
                .map((k) => (
                  <option key={k.code} value={k.code}>
                    {k.label}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          <AccessLabel text="Purchase source" accessKey={KEYS.vendor} />
          <select
            value={form.vendor_id}
            onChange={set('vendor_id')}
            {...accel(KEYS.vendor)}
          >
            <option value="">(none)</option>
            {sources.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <AccessLabel text="Account" accessKey={KEYS.account} />
          <input
            value={form.account_handle}
            onChange={set('account_handle')}
            {...accel(KEYS.account)}
          />
        </label>
        <label>
          <AccessLabel text="Listing link" accessKey={KEYS.template} />
          <input
            value={form.listing_url_template}
            onChange={set('listing_url_template')}
            placeholder="https://.../{external_id}"
            {...accel(KEYS.template)}
          />
        </label>
        <label>
          <AccessLabel text="Commission %" accessKey={KEYS.commission} />
          <input
            inputMode="decimal"
            value={form.commission_pct}
            onChange={set('commission_pct')}
            {...accel(KEYS.commission)}
          />
        </label>
        <label>
          <AccessLabel text="Processing %" accessKey={KEYS.processing} />
          <input
            inputMode="decimal"
            value={form.processing_pct}
            onChange={set('processing_pct')}
            {...accel(KEYS.processing)}
          />
        </label>
        <label>
          <AccessLabel text="Processing $ per sale" accessKey={KEYS.processingFixed} />
          <input
            inputMode="decimal"
            value={form.processing_fixed}
            onChange={set('processing_fixed')}
            {...accel(KEYS.processingFixed)}
          />
        </label>
        <label>
          <AccessLabel text="Fee $ per listing" accessKey={KEYS.listingFee} />
          <input
            inputMode="decimal"
            value={form.listing_fee}
            onChange={set('listing_fee')}
            {...accel(KEYS.listingFee)}
          />
        </label>
        <label>
          <AccessLabel text="Fees as of" accessKey={KEYS.termsAsOf} />
          <input
            type="date"
            value={form.terms_as_of}
            onChange={set('terms_as_of')}
            {...accel(KEYS.termsAsOf)}
          />
        </label>
        <label>
          <AccessLabel text="Notes" accessKey={KEYS.notes} />
          <input value={form.notes} onChange={set('notes')} {...accel(KEYS.notes)} />
        </label>
        {!isStore && !adding && (
          <label>
            <input
              type="checkbox"
              checked={!form.is_active}
              onChange={set('is_active')}
              {...accel(KEYS.retired)}
            />
            <AccessLabel text="Retired" accessKey={KEYS.retired} />
          </label>
        )}
      </div>
      {sample && (
        <p className="muted">
          Example link: <span>{sample}</span>
        </p>
      )}
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
 * The Platforms page: lists every sales venue and opens the add/edit form.
 */
export default function Platforms() {
  const [venues, setVenues] = useState(null)
  const [vendors, setVendors] = useState([])
  const [error, setError] = useState('')
  // null: nothing open; 'new': adding; otherwise the platform being edited.
  const [open, setOpen] = useState(null)
  const kinds = useReference('sales_venue_kind', { includeRetired: true }) ?? []
  const kindLabel = (code) => kinds.find((k) => k.code === code)?.label ?? code

  useEffect(() => {
    let cancelled = false
    Promise.all([api.listSalesVenues(), api.listVendors()])
      .then(([v, s]) => {
        if (cancelled) return
        setVenues(v)
        setVendors(s)
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  function saved(venue) {
    setVenues((current) => {
      const others = current.filter((v) => v.code !== venue.code)
      return [...others, venue].sort(
        (a, b) =>
          Number(b.is_own_store) - Number(a.is_own_store) ||
          a.name.localeCompare(b.name),
      )
    })
    setOpen(null)
  }

  if (error) return <p className="error">{error}</p>
  if (venues === null) return <p className="muted">Loading...</p>

  return (
    <section>
      <h1>Platforms</h1>
      <p className="muted">
        Where items are sold. Fees here are defaults for estimates.
      </p>
      <button onClick={() => setOpen('new')}>Add platform</button>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Kind</th>
            <th>Purchase source</th>
            <th>Account</th>
            <th>Default fees</th>
            <th>Fees as of</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {venues.map((v) => (
            <tr key={v.code} className={v.is_active ? '' : 'muted'}>
              <td>
                {v.name}
                {!v.is_active && ' (retired)'}
              </td>
              <td>{kindLabel(v.kind)}</td>
              <td>{v.vendor_name ?? ''}</td>
              <td>{v.account_handle ?? ''}</td>
              <td>{feeSummary(v)}</td>
              <td>{v.terms_as_of ?? ''}</td>
              <td>
                <button className="link" onClick={() => setOpen(v)}>
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open !== null && (
        <PlatformForm
          venue={open === 'new' ? null : open}
          venues={venues}
          vendors={vendors}
          onSaved={saved}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  )
}
