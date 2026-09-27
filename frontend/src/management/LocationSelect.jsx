import { useState } from 'react'

import { api } from './api'
import { useRequest } from '../shared/useRequest'
import { ReferenceSelect } from '../shared/reference'
import { orNull } from '../shared/text'

//: Location kinds other code makes -- a consignment by the auctions, a sold
//: item's by the sale -- and the server refuses from a picker.
const MADE_ELSEWHERE = new Set(['consigned', 'sold'])

const BLANK_DRAFT = { kind: '', institution: '', identifier: '' }

/**
 * Where an item is kept: a select over the storage locations, "--" for not
 * recorded, ending in "+ Add a location..." for an inline kind / place / box
 * form -- the vendor and seller pickers' shape. Optional everywhere it is
 * offered.
 *
 * `value` is the location's id as text ('' for none) and `onChange` receives
 * the same; the form turns it into a number or null when it sends it. A list
 * that fails to load leaves only "--" and the add: a location is never
 * required.
 */
export default function LocationSelect({ value, onChange, disabled = false }) {
  const loaded = useRequest('locations', () => api.listStorageLocations())
  const [added, setAdded] = useState([])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK_DRAFT)
  const [error, setError] = useState('')
  const locations = [...(loaded.data ?? []), ...added]

  async function addLocation() {
    try {
      const created = await api.createStorageLocation({
        kind: draft.kind,
        institution: orNull(draft.institution),
        identifier: orNull(draft.identifier),
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

  // Inside an entry form: Enter in a box adds the location rather than
  // submitting the form. A button keeps its own Enter.
  function onKeyDown(e) {
    if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
    e.preventDefault()
    if (draft.kind) addLocation()
  }

  if (adding) {
    return (
      <div className="add-reference" onKeyDown={onKeyDown}>
        <ReferenceSelect
          table="storage_location_kind"
          value={draft.kind}
          onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
          allowAdd={false}
          filter={(entry) => !MADE_ELSEWHERE.has(entry.code)}
        />
        <input
          placeholder="Bank or place"
          value={draft.institution}
          onChange={(e) => setDraft({ ...draft, institution: e.target.value })}
        />
        <input
          placeholder="Box or number"
          value={draft.identifier}
          onChange={(e) => setDraft({ ...draft, identifier: e.target.value })}
        />
        <button type="button" onClick={addLocation} disabled={!draft.kind}>
          Add location
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
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value === '__add__') setAdding(true)
        else onChange(e.target.value)
      }}
    >
      <option value="">--</option>
      {locations.map((loc) => (
        <option key={loc.id} value={String(loc.id)}>
          {loc.label}
        </option>
      ))}
      <option value="__add__">+ Add a location...</option>
    </select>
  )
}
