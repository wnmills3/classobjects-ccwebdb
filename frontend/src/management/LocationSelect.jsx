import { useState } from 'react'

import { api } from './api'
import { useRequest } from '../shared/useRequest'
import { ReferenceSelect } from '../shared/reference'
import { orNull } from '../shared/text'
import { useInlineAdd } from './useInlineAdd'

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
 * that fails to load leaves only "--" and the add, with the failure said
 * beside them: a location is never required.
 */
export default function LocationSelect({ value, onChange, disabled = false }) {
  const loaded = useRequest('locations', () => api.listStorageLocations())
  const [added, setAdded] = useState([])
  const locations = [...(loaded.data ?? []), ...added]
  const { adding, setAdding, draft, setDraft, error, add, wrapper, ready } =
    useInlineAdd({
      blank: BLANK_DRAFT,
      ready: (d) => Boolean(d.kind),
      create: (d) =>
        api.createStorageLocation({
          kind: d.kind,
          institution: orNull(d.institution),
          identifier: orNull(d.identifier),
        }),
      onCreated: (created) => {
        setAdded((list) => [...list, created])
        onChange(String(created.id))
      },
    })

  if (adding) {
    return (
      <div className="add-reference" ref={wrapper}>
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
        <button type="button" onClick={add} disabled={!ready}>
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
    <>
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
      {/* Said, because without the list "--" shows for an item that has a
          location as well as for one that has none. */}
      {loaded.error && (
        <span className="error">
          The storage locations could not be read: {loaded.error}
        </span>
      )}
    </>
  )
}
