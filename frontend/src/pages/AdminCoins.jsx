import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'
import { money } from '../format'

// Classifiers are sent as CODES, not free text: 'bullion', not 'Bullion';
// 'US', not 'United States'; 'MS64', not 'MS-64'. An unknown code comes back
// as a 422 naming the field, rather than silently storing nothing.
const BLANK = {
  title: '',
  description: '',
  item_kind: 'coin',
  country: '',
  denomination: '',
  grade: '',
  grading_service: '',
  metal: '',
  year_start: '',
  price: '',
  quantity_available: 0,
  is_active: true,
}

const KINDS = ['coin', 'currency', 'bullion', 'set', 'medal', 'token', 'other']

export default function AdminCoins() {
  const [items, setItems] = useState([])
  const [form, setForm] = useState(BLANK)
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      // include_inactive so administrators can see withdrawn items too.
      const page = await api.listCatalog({ include_inactive: true, limit: 200 })
      setItems(page.items)
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  function update(field) {
    return (event) => {
      const value =
        event.target.type === 'checkbox' ? event.target.checked : event.target.value
      setForm((f) => ({ ...f, [field]: value }))
    }
  }

  function startEdit(coin) {
    setEditingId(coin.id)
    // Only the fields the form owns: spreading the whole response would put
    // read-only values such as inventory_item_id into the PATCH payload.
    const editable = Object.fromEntries(
      Object.keys(BLANK).map((k) => [k, coin[k] ?? BLANK[k]]),
    )
    setForm({ ...editable, year_start: coin.year_start ?? '' })
    setError('')
  }

  function cancelEdit() {
    setEditingId(null)
    setForm(BLANK)
    setError('')
  }

  async function submit(event) {
    event.preventDefault()
    setError('')

    // Send absent values as null rather than "": an empty string is a value
    // the backend would try to resolve as a classifier code and reject.
    const payload = { ...form }
    payload.year_start = form.year_start === '' ? null : Number(form.year_start)
    payload.quantity_available = Number(payload.quantity_available)
    for (const key of ['country', 'denomination', 'grade', 'grading_service', 'metal']) {
      if (payload[key] === '') payload[key] = null
    }

    try {
      if (editingId) {
        await api.updateCatalogItem(editingId, payload)
      } else {
        await api.createCatalogItem(payload)
      }
      cancelEdit()
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function remove(coin) {
    if (!window.confirm(`Delete ${coin.title}? This cannot be undone.`)) return
    try {
      await api.deleteCatalogItem(coin.id)
      await load()
    } catch (err) {
      // 409 when the item appears in existing orders.
      setError(err.message)
    }
  }

  return (
    <section>
      <h1>Manage inventory</h1>
      {error && <p className="error">{error}</p>}

      <form onSubmit={submit} className="admin-form">
        <h2>{editingId ? `Edit item #${editingId}` : 'Add an item'}</h2>
        <div className="form-grid">
          <label>
            Title
            <input required value={form.title} onChange={update('title')} />
          </label>
          <label>
            Type
            <select value={form.item_kind} onChange={update('item_kind')}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label>
            Country code
            <input value={form.country} onChange={update('country')} placeholder="US" />
          </label>
          <label>
            Year
            <input
              type="number"
              value={form.year_start}
              onChange={update('year_start')}
            />
          </label>
          <label>
            Denomination code
            <input
              value={form.denomination}
              onChange={update('denomination')}
              placeholder="usd_coin_1_00"
            />
          </label>
          <label>
            Grade code
            <input value={form.grade} onChange={update('grade')} placeholder="MS64" />
          </label>
          <label>
            Graded by
            <input
              value={form.grading_service}
              onChange={update('grading_service')}
              placeholder="PCGS"
            />
          </label>
          <label>
            Metal code
            <input value={form.metal} onChange={update('metal')} placeholder="silver" />
          </label>
          <label>
            Price
            <input
              required
              type="number"
              step="0.01"
              min="0"
              value={form.price}
              onChange={update('price')}
            />
          </label>
          <label>
            Quantity for sale
            <input
              type="number"
              min="0"
              value={form.quantity_available}
              onChange={update('quantity_available')}
            />
          </label>
        </div>
        <label>
          Description
          <textarea rows={3} value={form.description} onChange={update('description')} />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={form.is_active} onChange={update('is_active')} />
          Listed for sale
        </label>
        <div className="row">
          <button type="submit">{editingId ? 'Save changes' : 'Add item'}</button>
          {editingId && (
            <button type="button" className="link" onClick={cancelEdit}>
              Cancel
            </button>
          )}
        </div>
      </form>

      <h2>Inventory ({items.length})</h2>
      {busy ? (
        <p className="muted">Loading...</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Item code</th>
              <th>Title</th>
              <th>Price</th>
              <th>Qty</th>
              <th>Listed</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((coin) => (
              <tr key={coin.id} className={coin.is_active ? '' : 'dim'}>
                <td className="mono">{coin.item_code}</td>
                <td>{coin.title}</td>
                <td>{money(coin.price)}</td>
                <td>{coin.quantity_available}</td>
                <td>{coin.is_active ? 'yes' : 'no'}</td>
                <td className="row">
                  <button className="link" onClick={() => startEdit(coin)}>
                    Edit
                  </button>
                  <button className="link danger" onClick={() => remove(coin)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
