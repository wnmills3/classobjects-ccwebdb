import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'
import { money } from '../format'

const BLANK = {
  sku: '',
  title: '',
  description: '',
  kind: 'coin',
  country: '',
  year: '',
  denomination: '',
  composition: '',
  grade: '',
  certification: '',
  mint_mark: '',
  price: '',
  quantity: 0,
  image_url: '',
  is_active: true,
}

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
      const page = await api.listCoins({ include_inactive: true, limit: 200 })
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
    setForm({ ...BLANK, ...coin, year: coin.year ?? '' })
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

    // Send year as null rather than "" so the backend sees an absent value.
    const payload = { ...form, year: form.year === '' ? null : Number(form.year) }
    payload.quantity = Number(payload.quantity)

    try {
      if (editingId) {
        const { sku, created_at, updated_at, id, ...editable } = payload
        await api.updateCoin(editingId, editable)
      } else {
        await api.createCoin(payload)
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
      await api.deleteCoin(coin.id)
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
            SKU
            <input
              required
              value={form.sku}
              onChange={update('sku')}
              disabled={Boolean(editingId)}
            />
          </label>
          <label>
            Title
            <input required value={form.title} onChange={update('title')} />
          </label>
          <label>
            Type
            <select value={form.kind} onChange={update('kind')}>
              <option value="coin">Coin</option>
              <option value="banknote">Banknote</option>
            </select>
          </label>
          <label>
            Country
            <input value={form.country} onChange={update('country')} />
          </label>
          <label>
            Year
            <input type="number" value={form.year} onChange={update('year')} />
          </label>
          <label>
            Denomination
            <input value={form.denomination} onChange={update('denomination')} />
          </label>
          <label>
            Composition
            <input value={form.composition} onChange={update('composition')} />
          </label>
          <label>
            Grade
            <input value={form.grade} onChange={update('grade')} />
          </label>
          <label>
            Certification
            <input value={form.certification} onChange={update('certification')} />
          </label>
          <label>
            Mint mark
            <input value={form.mint_mark} onChange={update('mint_mark')} />
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
            Quantity
            <input
              type="number"
              min="0"
              value={form.quantity}
              onChange={update('quantity')}
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
              <th>SKU</th>
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
                <td className="mono">{coin.sku}</td>
                <td>{coin.title}</td>
                <td>{money(coin.price)}</td>
                <td>{coin.quantity}</td>
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
