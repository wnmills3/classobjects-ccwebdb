import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api'
import NewItemForm from './entry/NewItemForm'
import SellerField from './SellerField'
import ItemEditDialog from './inventory/ItemEditDialog'
import HelpScope from '../HelpScope'
import { namesPurchase, purchaseNumber } from '../purchase-number'
import { ReferenceSelect } from '../../shared/reference'
import { date } from '../../shared/format'
import { orNull } from '../../shared/text'
import { SaveButton, SaveShortcut } from '../SaveButton'

/**
 * Recording an acquisition: a vendor and a purchase, then the items bought
 * on it.
 *
 * No item is ever entered outside a purchase -- a standalone buy is a
 * purchase holding one item -- so this page is the one door into the
 * console's item-creation path, and it is two steps on one page rather than
 * a wizard: pick or create the purchase, then the item form beneath it stays
 * open for as many items as the purchase actually had.
 */

//: 0..1 with up to 4 decimal places, the leading digit optional -- the server
//: (a Pydantic `Decimal`) accepts ".0635" exactly as it accepts "0.0635".
const RATE = /^(1(\.0{1,4})?|0?\.\d{1,4}|0)$/

/** Whether `text` is a tax rate the schema accepts: 0..1, up to 4 places. */
function isValidRate(text) {
  return RATE.test(text.trim())
}

const BLANK_VENDOR_DRAFT = { name: '', vendor_kind: '', url: '' }

/**
 * The vendor picker: a plain select over `listVendors()`, with a trailing
 * "+ Add a vendor..." option that opens an inline name / kind / web address
 * form -- the same idea as `ReferenceSelect`'s inline add, but vendors are
 * rows with their own id, not a reference table's codes, so this is its own
 * small component rather than a reuse of that one.
 */
function VendorField({ vendors, value, onChange, onVendorAdded }) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK_VENDOR_DRAFT)
  const [error, setError] = useState('')

  async function addVendor() {
    try {
      const created = await api.createVendor({
        name: draft.name.trim(),
        vendor_kind: draft.vendor_kind || null,
        url: orNull(draft.url),
      })
      onVendorAdded(created)
      onChange(created.id)
      setAdding(false)
      setDraft(BLANK_VENDOR_DRAFT)
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }

  // This block sits inside the purchase's own <form>: without this handler,
  // Enter in a text input submits the nearest form -- the outer purchase,
  // for whatever vendor was already picked -- instead of adding the vendor
  // being typed here.
  // A button already does the right thing with Enter -- swallowing its
  // default here would cancel Cancel's own activation and add the vendor the
  // user was trying to abandon.
  function onKeyDown(e) {
    if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
    e.preventDefault()
    if (draft.name.trim()) addVendor()
  }

  if (adding) {
    return (
      <div className="add-reference" onKeyDown={onKeyDown}>
        <input
          placeholder="Vendor name"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <ReferenceSelect
          table="vendor_kind"
          value={draft.vendor_kind}
          onChange={(e) => setDraft({ ...draft, vendor_kind: e.target.value })}
          placeholder="vendor kind"
        />
        <input
          placeholder="https://"
          value={draft.url}
          onChange={(e) => setDraft({ ...draft, url: e.target.value })}
        />
        <button type="button" onClick={addVendor} disabled={!draft.name.trim()}>
          Add
        </button>
        <button type="button" className="link" onClick={() => setAdding(false)}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    )
  }

  return (
    <select
      value={value ?? ''}
      onChange={(e) => {
        if (e.target.value === '__add__') setAdding(true)
        else onChange(Number(e.target.value))
      }}
      aria-label="Vendor"
    >
      <option value="">--</option>
      {vendors.map((v) => (
        <option key={v.id} value={v.id}>
          {v.name}
        </option>
      ))}
      <option value="__add__">+ Add a vendor...</option>
    </select>
  )
}

/**
 * The purchase's web address when it is a lot's page, or null. At eBay and
 * Whatnot an order holds many listings and its page is no listing's; the
 * backend's `app.listing_links` draws the same line.
 */
function lotPage(purchase) {
  return /ebay|whatnot/i.test(purchase.vendor ?? '')
    ? null
    : (purchase.source_url ?? null)
}

/** A seller picker's text as the API takes it: an id, or null for none. */
function sellerId(text) {
  return text ? Number(text) : null
}

/**
 * Whether `order` matches a filter typed against its order number, its
 * vendor, or -- exactly -- its own purchase number (`3974` or `#3974`).
 */
function matchesFilter(order, filterText) {
  const q = filterText.trim().toLowerCase()
  if (!q) return true
  return (
    namesPurchase(q, order.id) ||
    (order.order_number ?? '').toLowerCase().includes(q) ||
    order.vendor.toLowerCase().includes(q)
  )
}

//: The columns of the purchases table: heading, the field it shows, and the
//: direction a first click sorts in -- newest first for a date or a purchase
//: number, A to Z for text.
const PURCHASE_COLUMNS = [
  ['Order number', 'order_number', 'asc'],
  ['Date', 'ordered_on', 'desc'],
  ['Vendor', 'vendor', 'asc'],
  ['No.', 'id', 'desc'],
]

//: Order numbers and vendors compared as people read them: B-9 before B-10,
//: and eBay beside ebay rather than after every capital.
const byText = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Newest first: by date, an undated purchase last, then the latest entered. */
function newest(a, b) {
  if (a.ordered_on !== b.ordered_on) {
    if (!a.ordered_on || !b.ordered_on) return a.ordered_on ? -1 : 1
    return b.ordered_on.localeCompare(a.ordered_on)
  }
  return b.id - a.id
}

/**
 * The purchases in the order asked for. A purchase with nothing in the sorted
 * column goes last whichever way it runs -- an undated purchase is not the
 * oldest or the newest -- and ties, one vendor's purchases say, list newest
 * first.
 */
function sortPurchases(orders, { key, desc }) {
  return [...orders].sort((a, b) => {
    const x = a[key] || null
    const y = b[key] || null
    if (x === null || y === null) {
      return x === y ? newest(a, b) : x === null ? 1 : -1
    }
    // ISO dates sort as text; the collator reads the rest.
    const order = key === 'ordered_on' ? x.localeCompare(y) : byText.compare(x, y)
    return (desc ? -order : order) || newest(a, b)
  })
}

/**
 * The purchase orders to add to, as a table sorted by any of its columns --
 * newest first until another is chosen.
 *
 * The sort controls are buttons in the headers, and each row's order number
 * is the button that picks it, so the whole table works from the keyboard.
 */
function ExistingPurchasePicker({ orders, filterText, onPick }) {
  const [sort, setSort] = useState({ key: 'ordered_on', desc: true })
  if (orders.length === 0) {
    return <p className="muted">No purchases recorded yet.</p>
  }
  const shown = sortPurchases(
    orders.filter((order) => matchesFilter(order, filterText)),
    sort,
  )
  if (shown.length === 0) {
    return <p className="muted">No purchases match "{filterText}".</p>
  }

  function sortBy(key, first) {
    setSort((current) =>
      current.key === key
        ? { key, desc: !current.desc }
        : { key, desc: first === 'desc' },
    )
  }

  return (
    <table className="table purchase-table" aria-label="Purchases">
      <thead>
        <tr>
          {PURCHASE_COLUMNS.map(([label, key, first]) => {
            const active = sort.key === key
            const direction = sort.desc ? 'descending' : 'ascending'
            return (
              <th key={key} aria-sort={active ? direction : 'none'}>
                <button
                  type="button"
                  className="link sort-header"
                  onClick={() => sortBy(key, first)}
                >
                  {label}
                  {active && <span aria-hidden="true">{sort.desc ? ' ▼' : ' ▲'}</span>}
                </button>
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {shown.map((order) => (
          <tr key={order.id} className="purchase-row" onClick={() => onPick(order.id)}>
            <td>
              <button
                type="button"
                className="link"
                onClick={(e) => {
                  // The row picks it too; once is enough.
                  e.stopPropagation()
                  onPick(order.id)
                }}
              >
                {order.order_number || 'no order number'}
              </button>
            </td>
            <td>{date(order.ordered_on)}</td>
            <td>{order.vendor}</td>
            <td className="mono">{purchaseNumber(order.id)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const BLANK_PURCHASE = {
  vendor_id: '',
  order_number: '',
  ordered_on: '',
  source_url: '',
  seller_id: '',
  notes: '',
}

/** A purchase's editable details as form text: absent is ''. */
function detailsOf(purchase) {
  return {
    order_number: purchase.order_number ?? '',
    ordered_on: purchase.ordered_on ?? '',
    source_url: purchase.source_text ?? '',
    seller_id: purchase.seller_id == null ? '' : String(purchase.seller_id),
    notes: purchase.notes ?? '',
  }
}

/**
 * A purchase's number, date, web address, seller and notes, shown and changed.
 *
 * Sends only what changed. An order number cleared is given the next
 * generated one by the server (`Order-0001`, ...), never left blank.
 */
function PurchaseDetails({ purchase, onSaved }) {
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  if (draft === null) {
    return (
      <p className="row">
        <button
          type="button"
          className="link"
          data-help="edit_purchase"
          onClick={() => {
            setError('')
            setDraft(detailsOf(purchase))
          }}
        >
          Edit details
        </button>
      </p>
    )
  }

  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

  // From the form's submit, or from Ctrl+S, which has no event to stop.
  async function save(e) {
    e?.preventDefault()
    const was = detailsOf(purchase)
    const changes = Object.fromEntries(
      Object.entries(draft)
        .filter(([key, value]) => value !== was[key])
        .map(([key, value]) => [
          key,
          key === 'seller_id' ? sellerId(value) : orNull(value),
        ]),
    )
    // A purchase recorded with no number is given one on any save, even
    // with the box left blank: that is what finding it again needs.
    if (!purchase.order_number && !('order_number' in changes)) {
      changes.order_number = null
    }
    setSaving(true)
    try {
      const updated =
        Object.keys(changes).length > 0
          ? await api.updatePurchaseOrder(purchase.id, changes)
          : purchase
      setDraft(null)
      onSaved(updated)
    } catch (err) {
      // Kept open as typed: a 409 (that vendor already has the number) or a
      // 422 is corrected here.
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    // noValidate: a stored web address may be free text ("Gift"), and the
    // browser would refuse to submit the form at all over a box not being
    // changed. Only a changed value is sent, and the server checks it.
    <form className="admin-form" onSubmit={save} noValidate>
      {error && <p className="error">{error}</p>}
      <div className="form-grid">
        <label data-help="order_number">
          Order number{/* */}
          <input
            value={draft.order_number}
            onChange={set('order_number')}
            placeholder="blank: the next Order-0001, ..."
          />
        </label>
        <label data-help="ordered_on">
          Order date{/* */}
          <input type="date" value={draft.ordered_on} onChange={set('ordered_on')} />
        </label>
        <label data-help="source_url">
          Web address{/* */}
          <input
            type="url"
            placeholder="https://"
            value={draft.source_url}
            onChange={set('source_url')}
          />
        </label>
        <label data-help="seller_id">
          Seller{/* */}
          <SellerField
            value={draft.seller_id}
            onChange={(id) => setDraft({ ...draft, seller_id: id })}
          />
        </label>
      </div>
      <label data-help="purchase_notes">
        Notes{/* */}
        <textarea rows={2} value={draft.notes} onChange={set('notes')} />
      </label>
      <div className="row">
        <SaveButton
          type="submit"
          label="Save details"
          saving={saving}
          disabled={saving}
        />
        {/* The form stays mounted while closed, so the key is held only
            while it is open. */}
        <SaveShortcut onSave={save} enabled={!saving} />
        <button type="button" className="link" onClick={() => setDraft(null)}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export default function NewPurchase() {
  const rateId = useId()
  const [mode, setMode] = useState('new')
  const [vendors, setVendors] = useState(null)
  const [vendorsError, setVendorsError] = useState('')
  const [orders, setOrders] = useState(null)
  const [ordersError, setOrdersError] = useState('')
  const [orderFilter, setOrderFilter] = useState('')
  const [form, setForm] = useState(BLANK_PURCHASE)
  const [creating, setCreating] = useState(false)
  const [purchaseError, setPurchaseError] = useState('')
  const [purchase, setPurchase] = useState(null)
  //: Bumped to re-run the order-list effect. See `startAnother`.
  const [ordersEpoch, setOrdersEpoch] = useState(0)
  const [pickError, setPickError] = useState('')
  const [reloadError, setReloadError] = useState('')
  //: Guards against a stale `getPurchaseOrder` response: only the response
  //: matching the most recently requested pick is ever applied, so the
  //: older of two racing picks cannot overwrite the newer one just because
  //: its response happens to arrive last.
  const pickToken = useRef(0)

  /**
   * Read purchase `id` and show it, unless a later pick, reload or "Start
   * another purchase" has moved on by the time it answers. A failure is
   * handed to `onError`, under the same guard.
   */
  const fetchPurchase = useCallback((id, onError) => {
    const token = ++pickToken.current
    api
      .getPurchaseOrder(id)
      .then((body) => {
        if (pickToken.current === token) setPurchase(body)
      })
      .catch((err) => {
        if (pickToken.current === token) onError(err.message)
      })
  }, [])

  // Tax defaults for every item entered on this purchase. The rate box
  // starts empty, meaning "use the configured default" (`tax_rate: null`);
  // ticking "No sales tax charged" is what actually sends a zero rate.
  const [rateText, setRateText] = useState('')
  const [noTax, setNoTax] = useState(false)
  //: '' (as configured, sends null), 'true' or 'false' -- a plain checkbox
  //: cannot say "not taxed" separately from "use the configured default",
  //: and the configured default here is true.
  const [taxIncludesShipping, setTaxIncludesShipping] = useState('')

  useEffect(() => {
    let cancelled = false
    api
      .listVendors()
      .then((body) => {
        if (!cancelled) {
          setVendors(body)
          setVendorsError('')
        }
      })
      .catch((err) => {
        if (!cancelled) setVendorsError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const loadOrders = useCallback(() => {
    let cancelled = false
    api
      .listPurchaseOrders()
      .then((body) => {
        if (!cancelled) {
          setOrders(body)
          setOrdersError('')
        }
      })
      .catch((err) => {
        if (!cancelled) setOrdersError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(loadOrders, [loadOrders, ordersEpoch])

  // `?order=<id>` opens that purchase: the item editor links here so an
  // item's purchase can be corrected from the item.
  const [searchParams] = useSearchParams()
  const linkedOrder = Number(searchParams.get('order')) || null
  useEffect(() => {
    if (linkedOrder === null) return undefined
    fetchPurchase(linkedOrder, setPickError)
    return () => {
      pickToken.current += 1
    }
  }, [linkedOrder, fetchPurchase])

  function set(key) {
    return (e) => setForm({ ...form, [key]: e.target.value })
  }

  function pickExisting(id) {
    setPickError('')
    fetchPurchase(id, setPickError)
  }

  async function createPurchase(e) {
    e.preventDefault()
    setCreating(true)
    try {
      const payload = {
        vendor_id: Number(form.vendor_id),
        order_number: orNull(form.order_number),
        ordered_on: form.ordered_on || null,
        source_url: orNull(form.source_url),
        seller_id: sellerId(form.seller_id),
        notes: orNull(form.notes),
      }
      const created = await api.createPurchaseOrder(payload)
      setPurchase(created)
      setPurchaseError('')
    } catch (err) {
      // Kept in place: a 409 (duplicate order for that vendor) or a 422
      // shows here with the form exactly as typed, ready to correct.
      setPurchaseError(err.message)
    } finally {
      setCreating(false)
    }
  }

  // The item open in the editor, from the items table: an entry fixed where
  // it was made rather than found again on the inventory screens.
  const [editing, setEditing] = useState(null)

  // Saved or only closed, the purchase is read again: an error recorded, or
  // a filed photograph re-roled or removed, saves on its own in the editor.
  function closeEditor() {
    setEditing(null)
    reloadPurchase()
  }

  // Guarded by the same token a pick takes: a reload landing after "Start
  // another purchase", another pick, or a later reload must not put an old
  // answer back on screen.
  function reloadPurchase() {
    if (!purchase) return
    fetchPurchase(purchase.id, setReloadError)
  }

  function startAnother() {
    // Any reload still in flight is for the purchase being left.
    pickToken.current += 1
    setPurchase(null)
    setPurchaseError('')
    setForm(BLANK_PURCHASE)
    setPickError('')
    setReloadError('')
    setOrderFilter('')
    setRateText('')
    setNoTax(false)
    setTaxIncludesShipping('')
    setMode('new')
    // The purchase just finished is not on the list this page fetched when it
    // first loaded. Re-run the effect rather than calling `loadOrders()` here:
    // it returns a cancel function, and only an effect will actually call it,
    // so an imperative call leaves a fetch able to set state after unmount.
    setOrdersEpoch((n) => n + 1)
  }

  // Both derived, never stored: a remembered error and the text it was about
  // drift apart. Ticking "No sales tax charged" disables the rate box, and a
  // rate the user can no longer reach must not go on blocking saves -- so
  // `noTax` settles the question before the text is looked at.
  const rateInvalid = !noTax && rateText.trim() !== '' && !isValidRate(rateText)
  const rateError = rateInvalid
    ? 'Enter a rate between 0 and 1, with up to 4 decimal places.'
    : ''

  // What every item entered below is created with: an explicit rate wins,
  // "No sales tax charged" forces zero, and otherwise the server's own
  // configured default is used -- there is no endpoint that exposes it here.
  // An invalid rate is never passed down at all: saving is disabled instead
  // (see `itemDisabledReason`), so this only has to describe a valid state.
  const itemDefaults = {
    tax_rate: noTax ? '0' : rateInvalid ? null : orNull(rateText),
    tax_includes_shipping:
      taxIncludesShipping === '' ? null : taxIncludesShipping === 'true',
  }

  const itemDisabledReason = rateInvalid
    ? 'Fix the tax rate above before saving items.'
    : ''

  if (purchase) {
    return (
      <section>
        <h1>Purchases</h1>
        <HelpScope>
          <div className="admin-form">
            <h2>
              {purchase.order_number || <span className="muted">no order number</span>}
              {' · '}
              {purchase.vendor}
              {' · '}
              {date(purchase.ordered_on)}
              {purchase.source_url && (
                <>
                  {' · '}
                  <a
                    href={purchase.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Vendor page
                  </a>
                </>
              )}
              {purchase.seller && (
                <>
                  {' · Seller '}
                  {purchase.seller_url ? (
                    <a
                      href={purchase.seller_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {purchase.seller}
                    </a>
                  ) : (
                    purchase.seller
                  )}
                </>
              )}
            </h2>
            <PurchaseDetails
              key={purchase.id}
              purchase={purchase}
              onSaved={(updated) => {
                setPurchase(updated)
                // The list shows the number: fetch it again for the picker.
                setOrdersEpoch((n) => n + 1)
              }}
            />
            {reloadError && <p className="error">{reloadError}</p>}

            <div className="filter-grid">
              <div data-help="tax_rate">
                <label htmlFor={rateId}>Tax rate</label>
                <input
                  id={rateId}
                  type="text"
                  inputMode="decimal"
                  value={rateText}
                  onChange={(e) => setRateText(e.target.value)}
                  disabled={noTax}
                  placeholder="leave blank for the configured rate"
                />
                <p className="muted">
                  Leave blank to use the configured default rate. Enter a decimal such
                  as 0.0635 for 6.35%.
                </p>
                {rateError && <p className="error">{rateError}</p>}
              </div>
              <label data-help="no_sales_tax" className="checkbox">
                <input
                  type="checkbox"
                  checked={noTax}
                  onChange={(e) => setNoTax(e.target.checked)}
                />
                {/* */}
                No sales tax charged
              </label>
              <label data-help="tax_includes_shipping">
                Tax on shipping{/* */}
                <select
                  value={taxIncludesShipping}
                  onChange={(e) => setTaxIncludesShipping(e.target.value)}
                >
                  <option value="">As configured</option>
                  <option value="true">Taxed</option>
                  <option value="false">Not taxed</option>
                </select>
              </label>
            </div>

            <table className="table">
              <thead>
                <tr>
                  <th>Item code</th>
                  <th>Title</th>
                  <th>Kind</th>
                  <th>Cost</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(purchase.lines ?? []).map((line) => (
                  <tr key={line.id}>
                    <td className="mono">
                      <button
                        type="button"
                        className="link mono"
                        onClick={() => setEditing(line.id)}
                      >
                        {line.item_code}
                      </button>
                    </td>
                    <td>{line.source_title}</td>
                    <td>{line.item_kind}</td>
                    <td>{line.item_cost}</td>
                    <td>{line.status}</td>
                  </tr>
                ))}
                {(purchase.lines ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      No items entered yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            {editing && (
              <ItemEditDialog
                key={editing}
                itemId={editing}
                onSaved={closeEditor}
                onChanged={reloadPurchase}
                onClose={closeEditor}
              />
            )}

            <NewItemForm
              purchaseOrderId={purchase.id}
              orderUrl={lotPage(purchase)}
              defaults={itemDefaults}
              onSaved={reloadPurchase}
              disabledReason={itemDisabledReason}
            />

            <p className="row">
              {/* A routed link, not a hard-coded shop path: the console mounts
                under basename "/management" (management/main.jsx), and a plain
                `href="/receiving?..."` would send the browser to the shop at
                the site root instead. */}
              <Link to={`/receiving?order=${purchase.id}`}>Receive these</Link>
              <button type="button" className="link" onClick={startAnother}>
                Start another purchase
              </button>
            </p>
          </div>
        </HelpScope>
      </section>
    )
  }

  return (
    <section>
      <h1>Purchases</h1>
      <HelpScope>
        <div className="filter-grid" data-help="purchase_mode">
          <label className="checkbox">
            <input
              type="radio"
              name="purchase-mode"
              value="existing"
              checked={mode === 'existing'}
              onChange={() => setMode('existing')}
            />
            {/* */}
            Add to an existing purchase
          </label>
          <label className="checkbox">
            <input
              type="radio"
              name="purchase-mode"
              value="new"
              checked={mode === 'new'}
              onChange={() => setMode('new')}
            />
            {/* */}
            Start a new purchase
          </label>
        </div>

        {mode === 'existing' && (
          <div className="admin-form">
            {ordersError && <p className="error">{ordersError}</p>}
            {pickError && <p className="error">{pickError}</p>}
            {!ordersError && !orders && <p className="muted">Loading...</p>}
            {orders && orders.length > 0 && (
              <label data-help="purchase_filter">
                Filter{/* */}
                <input
                  type="text"
                  value={orderFilter}
                  onChange={(e) => setOrderFilter(e.target.value)}
                  placeholder="Order number or vendor"
                />
              </label>
            )}
            {orders && (
              <ExistingPurchasePicker
                orders={orders}
                filterText={orderFilter}
                onPick={pickExisting}
              />
            )}
          </div>
        )}

        {mode === 'new' && (
          <form className="admin-form" onSubmit={createPurchase}>
            {purchaseError && <p className="error">{purchaseError}</p>}
            {vendorsError && <p className="error">{vendorsError}</p>}
            <div className="form-grid">
              <label data-help="vendor">
                Vendor{/* */}
                {vendors ? (
                  <VendorField
                    vendors={vendors}
                    value={form.vendor_id}
                    onChange={(id) => setForm({ ...form, vendor_id: id })}
                    onVendorAdded={(created) =>
                      setVendors((v) =>
                        [...v, created].sort((a, b) => a.name.localeCompare(b.name)),
                      )
                    }
                  />
                ) : (
                  <span className="muted">Loading...</span>
                )}
              </label>
              <label data-help="order_number">
                Order number{/* */}
                <input
                  value={form.order_number}
                  onChange={set('order_number')}
                  placeholder="blank: the next Order-0001, ..."
                />
              </label>
              <label data-help="ordered_on">
                Order date{/* */}
                <input
                  type="date"
                  value={form.ordered_on}
                  onChange={set('ordered_on')}
                />
              </label>
              <label data-help="source_url">
                Web address{/* */}
                <input
                  type="url"
                  placeholder="https://"
                  value={form.source_url}
                  onChange={set('source_url')}
                />
              </label>
              <label data-help="seller_id">
                Seller{/* */}
                <SellerField
                  value={form.seller_id}
                  onChange={(id) => setForm({ ...form, seller_id: id })}
                />
              </label>
            </div>
            <label data-help="purchase_notes">
              Notes{/* */}
              <textarea rows={2} value={form.notes} onChange={set('notes')} />
            </label>
            <div className="row">
              <button type="submit" disabled={creating || !form.vendor_id}>
                {creating ? 'Creating...' : 'Create purchase'}
              </button>
            </div>
          </form>
        )}
      </HelpScope>
    </section>
  )
}
