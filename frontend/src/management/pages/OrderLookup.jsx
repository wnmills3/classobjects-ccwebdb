import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import HelpScope from '../HelpScope'
import { purchaseNumber } from '../purchase-number'
import { matchesFilter, sortPurchases } from '../purchase-list'
import { centsOrZero, fromCents } from '../../shared/cents'
import { date, money } from '../../shared/format'
import { useRequest } from '../../shared/useRequest'

//: The table's columns: heading, the field it shows, and the direction a
//: first click sorts in -- newest or largest first for a date, a count or an
//: amount, A to Z for text.
const COLUMNS = [
  ['Order number', 'order_number', 'asc'],
  ['Date', 'ordered_on', 'desc'],
  ['Vendor', 'vendor', 'asc'],
  ['Seller', 'seller', 'asc'],
  ['Items', 'total', 'desc'],
  ['Not received', 'outstanding', 'desc'],
  ['Cost', 'total_cost', 'desc'],
  ['No.', 'id', 'desc'],
]

/**
 * Whether a purchase was ordered within the dates given, either end
 * included. An end left empty is open. A purchase with no order date is in
 * no range: it is listed only while both ends are empty.
 */
function inDates(order, from, to) {
  if (!from && !to) return true
  if (!order.ordered_on) return false
  return (!from || order.ordered_on >= from) && (!to || order.ordered_on <= to)
}

/**
 * Order lookup: find a purchase by what is remembered of it.
 *
 * Every purchase, narrowed as the boxes are filled: part of an order number,
 * a vendor or a seller (or a purchase number, exactly); a range of order
 * dates; and whether anything on it has still to arrive. The table sorts by
 * any column, newest first to begin with, and a purchase's order number
 * opens it on the Purchases page, where its items are listed and it can be
 * added to or corrected.
 *
 * Nothing is asked of the server as the boxes change: the list is read once
 * and narrowed here, so typing shows its result at once.
 */
export default function OrderLookup() {
  const loaded = useRequest('order-lookup', () => api.listPurchaseOrders())
  const [text, setText] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [openOnly, setOpenOnly] = useState(false)
  const [sort, setSort] = useState({ key: 'ordered_on', desc: true })

  const orders = loaded.data ?? []
  const shown = sortPurchases(
    orders.filter(
      (order) =>
        matchesFilter(order, text) &&
        inDates(order, from, to) &&
        (!openOnly || order.outstanding > 0),
    ),
    sort,
  )
  const cost = shown.reduce((sum, order) => sum + centsOrZero(order.total_cost), 0)

  function sortBy(key, first) {
    setSort((current) =>
      current.key === key
        ? { key, desc: !current.desc }
        : { key, desc: first === 'desc' },
    )
  }

  function clear() {
    setText('')
    setFrom('')
    setTo('')
    setOpenOnly(false)
  }

  return (
    <HelpScope>
      <h1>Order lookup</h1>
      <p className="muted">
        Find a purchase by what you remember of it. Click its order number to open it.
      </p>
      {loaded.error && <p className="error">{loaded.error}</p>}
      {!loaded.error && !loaded.data && <p className="muted">Loading...</p>}
      {loaded.data && (
        <>
          <div className="filter-grid">
            <label data-help="order_lookup_text">
              Order number, vendor or seller{/* */}
              <input
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="part of any of them, or #3974"
              />
            </label>
            <label data-help="order_lookup_from">
              Ordered from{/* */}
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </label>
            <label data-help="order_lookup_to">
              Ordered to{/* */}
              <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
            <label data-help="purchase_open_only" className="checkbox">
              <input
                type="checkbox"
                checked={openOnly}
                onChange={(e) => setOpenOnly(e.target.checked)}
              />
              {/* */}
              Only purchases with items not yet received
            </label>
            <button type="button" className="link" onClick={clear}>
              Clear
            </button>
          </div>
          <output className="status-line">
            {shown.length.toLocaleString()} of {orders.length.toLocaleString()}{' '}
            purchases, costing {money(fromCents(cost))}
          </output>
          {shown.length === 0 ? (
            <p className="muted">
              {orders.length === 0
                ? 'No purchases recorded yet.'
                : 'No purchase matches. Clear a box to widen the search.'}
            </p>
          ) : (
            <table className="table purchase-table" aria-label="Purchases found">
              <thead>
                <tr>
                  {COLUMNS.map(([label, key, first]) => {
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
                          {active && (
                            <span aria-hidden="true">{sort.desc ? ' ▼' : ' ▲'}</span>
                          )}
                        </button>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {shown.map((order) => (
                  <tr key={order.id}>
                    <td>
                      <Link to={`/purchases?order=${order.id}`}>
                        {order.order_number || 'no order number'}
                      </Link>
                    </td>
                    <td>{date(order.ordered_on)}</td>
                    <td>{order.vendor}</td>
                    <td>{order.seller ?? ''}</td>
                    <td>{order.total}</td>
                    <td>{order.outstanding || ''}</td>
                    <td>{money(order.total_cost)}</td>
                    <td className="mono">{purchaseNumber(order.id)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </HelpScope>
  )
}
