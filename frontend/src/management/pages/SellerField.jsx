import { useState } from 'react'

import { api } from '../api'
import { useRequest } from '../../shared/useRequest'
import { orNull } from '../../shared/text'

const BLANK_DRAFT = { name: '', store_url: '' }

/**
 * The seller picker: who sold a purchase on the marketplace its vendor
 * names. A select over `listSellers()`, ending in "+ Add a seller..." for an
 * inline name and store form -- the vendor picker's shape.
 *
 * `value` is the seller's id as text ('' for none), and `onChange` receives
 * the same, so it sits in a form of text fields; the form turns it into a
 * number or null when it sends it.
 */
export default function SellerField({ value, onChange }) {
  const loaded = useRequest('sellers', () => api.listSellers())
  const [added, setAdded] = useState([])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK_DRAFT)
  const [error, setError] = useState('')
  const sellers = [...(loaded.data ?? []), ...added].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
  )

  async function addSeller() {
    try {
      const created = await api.createSeller({
        name: draft.name.trim(),
        store_url: orNull(draft.store_url),
      })
      setAdded((list) => [...list, created])
      onChange(String(created.id))
      setAdding(false)
      setDraft(BLANK_DRAFT)
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }

  // Inside the purchase's own form: Enter in a box adds the seller rather
  // than submitting the purchase. A button keeps its own Enter.
  function onKeyDown(e) {
    if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
    e.preventDefault()
    if (draft.name.trim()) addSeller()
  }

  if (adding) {
    return (
      <div className="add-reference" onKeyDown={onKeyDown}>
        <input
          placeholder="Seller name"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <input
          placeholder="Store web address or email"
          value={draft.store_url}
          onChange={(e) => setDraft({ ...draft, store_url: e.target.value })}
        />
        <button type="button" onClick={addSeller} disabled={!draft.name.trim()}>
          Add seller
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
        else onChange(e.target.value)
      }}
      aria-label="Seller"
    >
      <option value="">--</option>
      {sellers.map((s) => (
        <option key={s.id} value={String(s.id)}>
          {s.name}
        </option>
      ))}
      <option value="__add__">+ Add a seller...</option>
    </select>
  )
}
