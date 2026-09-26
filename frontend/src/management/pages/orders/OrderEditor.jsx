import { useEffect, useState } from 'react'

import { api } from '../../api'
import { money } from '../../../shared/format'
import { AccessLabel } from '../../AccessLabel'
import { accel, useSaveShortcut } from '../../shortcuts'
import { fromCents, isMoney, toCents } from '../../../shared/cents'
import { totalCents } from './cents'

/**
 * Placing or revising an order on a customer's behalf.
 *
 * `order` is null for a new order. The whole desired contents are sent at
 * once -- the server moves stock by the difference, all or nothing -- with the
 * version the order was loaded at, so a save over someone else's change is
 * refused rather than silently applied.
 */
export default function OrderEditor({ order, onSaved, onClose }) {
  const [customers, setCustomers] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [customerKey, setCustomerKey] = useState(order ? `c:${order.customer_id}` : '')
  const [find, setFind] = useState('')
  const [lines, setLines] = useState(
    (order?.items ?? []).map((item) => ({
      listing_id: item.listing_id,
      title: item.title,
      quantity: String(item.quantity),
      unit_price: String(item.unit_price),
      listing_price: null,
      available: null,
    })),
  )
  const [notes, setNotes] = useState(order?.notes ?? '')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  // `save` is a function declaration below, hoisted for the whole component
  // scope, so it is safe to reference here even though it is defined later.
  useSaveShortcut(save, !saving)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.listCustomers(), api.listUsers()])
      .then(([customerRows, userRows]) => {
        if (cancelled) return
        setCustomers(customerRows)
        setAccounts(
          userRows.filter((u) => !customerRows.some((c) => c.user_id === u.id)),
        )
      })
      .catch((err) => !cancelled && setError(err.message))
    // Listing price and stock for lines already on the order.
    for (const item of order?.items ?? []) {
      api
        .getCatalogItem(item.listing_id)
        .then((listing) => {
          if (cancelled) return
          setLines((current) =>
            current.map((line) =>
              line.listing_id === item.listing_id
                ? {
                    ...line,
                    listing_price: listing.price,
                    available: listing.quantity_available,
                  }
                : line,
            ),
          )
        })
        .catch(() => {})
    }
    return () => {
      cancelled = true
    }
  }, [order])

  const options = [
    ...(customers ?? []).map((c) => ({
      key: `c:${c.id}`,
      label: c.email ? `${c.display_name} (${c.email})` : c.display_name,
    })),
    ...accounts.map((u) => ({
      key: `u:${u.id}`,
      label: `${u.full_name || u.email} (${u.email}), account, no orders yet`,
    })),
  ]
  const shown = options.filter(
    (o) => o.key === customerKey || o.label.toLowerCase().includes(find.toLowerCase()),
  )

  const setLine = (listingId, field) => (e) =>
    setLines(
      lines.map((l) =>
        l.listing_id === listingId ? { ...l, [field]: e.target.value } : l,
      ),
    )

  async function search() {
    try {
      const page = await api.listCatalog({ q: query, in_stock: true, limit: 10 })
      setResults(page.items)
    } catch (err) {
      setError(err.message)
    }
  }

  function add(listing) {
    if (lines.some((l) => l.listing_id === listing.id)) return
    setLines([
      ...lines,
      {
        listing_id: listing.id,
        title: listing.title,
        quantity: '1',
        unit_price: String(listing.price),
        listing_price: String(listing.price),
        available: listing.quantity_available,
      },
    ])
  }

  async function save() {
    setError('')
    if (!customerKey) return setError('Choose a customer.')
    if (lines.length === 0) {
      return setError('An order needs at least one item. To empty an order, cancel it.')
    }
    const bad = lines.find(
      (l) =>
        !/^\d+$/.test(l.quantity) || Number(l.quantity) < 1 || !isMoney(l.unit_price),
    )
    if (bad) return setError(`Check the quantity and price of ${bad.title}.`)

    setSaving(true)
    try {
      let customerId = Number(customerKey.slice(2))
      if (customerKey.startsWith('u:'))
        customerId = (await api.customerForUser(customerId)).id
      const items = lines.map((l) => ({
        listing_id: l.listing_id,
        quantity: Number(l.quantity),
        unit_price: fromCents(toCents(l.unit_price)),
      }))
      // Not `orNull`: notes go as typed, surrounding whitespace and all;
      // only a box with nothing but whitespace in it is no notes at all.
      const notesValue = notes.trim() === '' ? null : notes
      if (order) {
        await api.reviseOrder(order.id, {
          version: order.version,
          customer_id: customerId,
          items,
          notes: notesValue,
        })
      } else {
        await api.createOrderFor(customerId, { items, notes: notesValue })
      }
      onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>{order ? `Order #${order.id}` : 'New order'}</h2>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>

      {order?.status === 'paid' && (
        <p className="notice">
          This order is paid. Changing its total will flag a payment adjustment.
        </p>
      )}
      {error && <p className="error">{error}</p>}

      <div className="row">
        <input
          type="search"
          aria-label="Find customer"
          placeholder="Find customer"
          value={find}
          onChange={(e) => setFind(e.target.value)}
          {...accel('n')}
        />
        <select
          aria-label="Customer"
          value={customerKey}
          onChange={(e) => setCustomerKey(e.target.value)}
          {...accel('c')}
        >
          <option value="">Choose a customer</option>
          {shown.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Quantity</th>
            <th>Unit price</th>
            <th>Available</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.listing_id}>
              <td>{line.title}</td>
              <td>
                <input
                  type="number"
                  min="1"
                  aria-label={`Quantity of ${line.title}`}
                  value={line.quantity}
                  onChange={setLine(line.listing_id, 'quantity')}
                />
              </td>
              <td>
                <input
                  type="text"
                  inputMode="decimal"
                  aria-label={`Price of ${line.title}`}
                  value={line.unit_price}
                  onChange={setLine(line.listing_id, 'unit_price')}
                />
                {line.listing_price !== null &&
                  isMoney(line.unit_price) &&
                  isMoney(line.listing_price) &&
                  toCents(line.unit_price) !== toCents(line.listing_price) && (
                    <div className="muted">
                      listing price {money(line.listing_price)}
                    </div>
                  )}
              </td>
              <td className="muted">{line.available ?? '-'}</td>
              <td>
                <button
                  className="link"
                  aria-label={`Remove ${line.title}`}
                  onClick={() =>
                    setLines(lines.filter((l) => l.listing_id !== line.listing_id))
                  }
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="row">
        <input
          type="search"
          aria-label="Find item"
          placeholder="Find item"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          {...accel('i')}
        />
        <button onClick={search} {...accel('h')}>
          <AccessLabel text="Search" accessKey="h" />
        </button>
      </div>
      {results.map((listing) => (
        <div key={listing.id} className="row">
          <span>
            {listing.title}{' '}
            <span className="muted">
              {money(listing.price, listing.currency)}, {listing.quantity_available}{' '}
              available
            </span>
          </span>
          <button
            className="link"
            aria-label={`Add ${listing.title}`}
            onClick={() => add(listing)}
          >
            Add
          </button>
        </div>
      ))}

      <label className="field">
        <AccessLabel text="Notes" accessKey="o" />
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          {...accel('o')}
        />
      </label>

      <div className="row">
        <strong>Total {money(fromCents(totalCents(lines)))}</strong>
        <button disabled={saving} onClick={save} {...accel('v')}>
          <AccessLabel text={saving ? 'Saving...' : 'Save order'} accessKey="v" />
        </button>
      </div>
    </div>
  )
}
