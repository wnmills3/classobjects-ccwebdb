import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import NewItemForm from './entry/NewItemForm'
import HelpScope from '../HelpScope'
import { ReferenceSelect } from '../../shared/reference'
import { date } from '../../shared/format'

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
        url: draft.url.trim() || null,
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

/** Whether `order` matches a filter typed against its number or vendor. */
function matchesFilter(order, filterText) {
  const q = filterText.trim().toLowerCase()
  if (!q) return true
  return (
    (order.order_number ?? '').toLowerCase().includes(q) ||
    order.vendor.toLowerCase().includes(q)
  )
}

/**
 * The list of purchase orders to add to, newest concerns first.
 *
 * Rows are buttons, not bare `<li onClick>`s, so the list is reachable and
 * operable from the keyboard, not only a mouse.
 */
function ExistingPurchasePicker({ orders, filterText, onPick }) {
  if (orders.length === 0) {
    return <p className="muted">No purchases recorded yet.</p>
  }
  const shown = orders.filter((order) => matchesFilter(order, filterText))
  if (shown.length === 0) {
    return <p className="muted">No purchases match "{filterText}".</p>
  }
  return (
    <ul className="order-picker">
      {shown.map((order) => (
        <li key={order.id}>
          <button type="button" className="order-row" onClick={() => onPick(order.id)}>
            {order.order_number || <span className="muted">no order number</span>}
            {' · '}
            {order.vendor}
            {' · '}
            {date(order.ordered_on)}
          </button>
        </li>
      ))}
    </ul>
  )
}

const BLANK_PURCHASE = {
  vendor_id: '',
  order_number: '',
  ordered_on: '',
  source_url: '',
  notes: '',
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

  function set(key) {
    return (e) => setForm({ ...form, [key]: e.target.value })
  }

  function pickExisting(id) {
    setPickError('')
    const token = ++pickToken.current
    api
      .getPurchaseOrder(id)
      .then((body) => {
        if (pickToken.current === token) setPurchase(body)
      })
      .catch((err) => {
        if (pickToken.current === token) setPickError(err.message)
      })
  }

  async function createPurchase(e) {
    e.preventDefault()
    setCreating(true)
    try {
      const payload = {
        vendor_id: Number(form.vendor_id),
        order_number: form.order_number.trim() || null,
        ordered_on: form.ordered_on || null,
        source_url: form.source_url.trim() || null,
        notes: form.notes.trim() || null,
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

  function reloadPurchase() {
    if (!purchase) return
    api
      .getPurchaseOrder(purchase.id)
      .then(setPurchase)
      .catch((err) => setReloadError(err.message))
  }

  function startAnother() {
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
    tax_rate: noTax ? '0' : rateInvalid ? null : rateText.trim() || null,
    tax_includes_shipping:
      taxIncludesShipping === '' ? null : taxIncludesShipping === 'true',
  }

  const itemDisabledReason = rateInvalid
    ? 'Fix the tax rate above before saving items.'
    : ''

  if (purchase) {
    return (
      <section>
        <h1>New purchase</h1>
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
            </h2>
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
                    <td className="mono">{line.item_code}</td>
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

            <NewItemForm
              purchaseOrderId={purchase.id}
              defaults={itemDefaults}
              onSaved={reloadPurchase}
              disabledReason={itemDisabledReason}
            />

            <p className="row">
              {/* A routed link, not a hard-coded shop path: the console mounts
                under basename "/owner" (owner/main.jsx), and a plain
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
      <h1>New purchase</h1>
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
                <input value={form.order_number} onChange={set('order_number')} />
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
