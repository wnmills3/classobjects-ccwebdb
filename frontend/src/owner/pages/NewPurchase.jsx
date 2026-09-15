import { useCallback, useEffect, useId, useState } from 'react'

import { api } from '../api'
import NewItemForm from './entry/NewItemForm'
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

const RATE = /^(0(\.\d{1,4})?|1(\.0{1,4})?)$/

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

  if (adding) {
    return (
      <div className="add-reference">
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

/** The list of purchase orders to add to, newest concerns first. */
function ExistingPurchasePicker({ orders, onPick }) {
  if (orders.length === 0) {
    return <p className="muted">No purchases recorded yet.</p>
  }
  return (
    <ul className="order-picker">
      {orders.map((order) => (
        <li key={order.id} className="order-row" onClick={() => onPick(order.id)}>
          {order.order_number || <span className="muted">no order number</span>}
          {' · '}
          {order.vendor}
          {' · '}
          {date(order.ordered_on)}
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
  const [form, setForm] = useState(BLANK_PURCHASE)
  const [creating, setCreating] = useState(false)
  const [purchaseError, setPurchaseError] = useState('')
  const [purchase, setPurchase] = useState(null)
  const [pickError, setPickError] = useState('')

  // Tax defaults for every item entered on this purchase. The rate box
  // starts empty, meaning "use the configured default" (`tax_rate: null`);
  // ticking "No sales tax charged" is what actually sends a zero rate.
  const [rateText, setRateText] = useState('')
  const [rateError, setRateError] = useState('')
  const [noTax, setNoTax] = useState(false)
  const [taxIncludesShipping, setTaxIncludesShipping] = useState(false)

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

  useEffect(loadOrders, [loadOrders])

  function set(key) {
    return (e) => setForm({ ...form, [key]: e.target.value })
  }

  function pickExisting(id) {
    setPickError('')
    api
      .getPurchaseOrder(id)
      .then(setPurchase)
      .catch((err) => setPickError(err.message))
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
    if (purchase) api.getPurchaseOrder(purchase.id).then(setPurchase)
  }

  function onRateChange(e) {
    const text = e.target.value
    setRateText(text)
    setRateError(
      text.trim() !== '' && !isValidRate(text)
        ? 'Enter a rate between 0 and 1, with up to 4 decimal places.'
        : '',
    )
  }

  // What every item entered below is created with: an explicit rate wins,
  // "No sales tax charged" forces zero, and otherwise the server's own
  // configured default is used -- there is no endpoint that exposes it here.
  const itemDefaults = {
    tax_rate: noTax ? '0' : rateText.trim() === '' ? null : rateText.trim(),
    tax_includes_shipping: taxIncludesShipping ? true : null,
  }

  if (purchase) {
    return (
      <section>
        <h1>New purchase</h1>
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
                <a href={purchase.source_url} target="_blank" rel="noopener noreferrer">
                  Vendor page
                </a>
              </>
            )}
          </h2>

          <div className="filter-grid">
            <div>
              <label htmlFor={rateId}>Tax rate</label>
              <input
                id={rateId}
                type="text"
                inputMode="decimal"
                value={rateText}
                onChange={onRateChange}
                disabled={noTax}
                placeholder="leave blank for the configured rate"
              />
              <p className="muted">
                Leave blank to use the configured default rate. Enter a decimal such as
                0.0635 for 6.35%.
              </p>
              {rateError && <p className="error">{rateError}</p>}
            </div>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={noTax}
                onChange={(e) => setNoTax(e.target.checked)}
              />
              {/* */}
              No sales tax charged
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={taxIncludesShipping}
                onChange={(e) => setTaxIncludesShipping(e.target.checked)}
              />
              {/* */}
              Tax includes shipping
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
                  <td>{line.source_title ?? line.description}</td>
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
          />

          <p>
            <a href={`/receiving?order=${purchase.id}`}>Receive these</a>
          </p>
        </div>
      </section>
    )
  }

  return (
    <section>
      <h1>New purchase</h1>

      <div className="filter-grid">
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
          {orders && <ExistingPurchasePicker orders={orders} onPick={pickExisting} />}
        </div>
      )}

      {mode === 'new' && (
        <form className="admin-form" onSubmit={createPurchase}>
          {purchaseError && <p className="error">{purchaseError}</p>}
          {vendorsError && <p className="error">{vendorsError}</p>}
          <div className="form-grid">
            <label>
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
            <label>
              Order number{/* */}
              <input value={form.order_number} onChange={set('order_number')} />
            </label>
            <label>
              Order date{/* */}
              <input type="date" value={form.ordered_on} onChange={set('ordered_on')} />
            </label>
            <label>
              Web address{/* */}
              <input
                type="url"
                placeholder="https://"
                value={form.source_url}
                onChange={set('source_url')}
              />
            </label>
          </div>
          <label>
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
    </section>
  )
}
