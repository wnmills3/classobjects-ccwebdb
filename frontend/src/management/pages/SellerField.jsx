import { useState } from 'react'

import { api } from '../api'
import { useRequest } from '../../shared/useRequest'
import { siteName, withScheme } from '../site-name'
import { useInlineAdd } from '../useInlineAdd'

const BLANK_DRAFT = { name: '', store_url: '' }

/**
 * The seller picker: who sold a purchase on the marketplace its vendor
 * names. A select over `listSellers()`, ending in "+ Add a seller..." for an
 * inline store and name form -- the vendor picker's shape: the store's
 * address is asked first and proposes the name (`siteName`).
 *
 * `value` is the seller's id as text ('' for none), and `onChange` receives
 * the same, so it sits in a form of text fields; the form turns it into a
 * number or null when it sends it.
 */
export default function SellerField({ value, onChange }) {
  const loaded = useRequest('sellers', () => api.listSellers())
  const [added, setAdded] = useState([])
  const sellers = [...(loaded.data ?? []), ...added].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
  )
  const { adding, setAdding, draft, setDraft, error, add, onKeyDown, ready } =
    useInlineAdd({
      blank: BLANK_DRAFT,
      ready: (d) => Boolean(d.name.trim()),
      create: (d) =>
        api.createSeller({
          name: d.name.trim(),
          store_url: withScheme(d.store_url),
        }),
      onCreated: (created) => {
        setAdded((list) => [...list, created])
        onChange(String(created.id))
      },
    })

  if (adding) {
    return (
      <div className="add-reference" onKeyDown={onKeyDown}>
        {/* The store first: a seller is named for their place on the
            marketplace, so its address proposes the name. Only a name the
            address gave follows it, or an empty one, which is nobody's
            choice; one typed by hand is left alone. */}
        <input
          placeholder="Store web address or email"
          aria-label="Store web address or email"
          value={draft.store_url}
          autoFocus
          onChange={(e) => {
            const store = e.target.value
            const proposed =
              draft.name === '' || draft.name === siteName(draft.store_url)
            setDraft({
              ...draft,
              store_url: store,
              name: proposed ? siteName(store) : draft.name,
            })
          }}
        />
        <input
          placeholder="Seller name"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <button type="button" onClick={add} disabled={!ready}>
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
    <>
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
      {/* A list that could not be read is not an empty one. */}
      {loaded.error && <span className="error">{loaded.error}</span>}
    </>
  )
}
