import { useState } from 'react'

import { api } from './api'
import { ADD, useAddOption } from '../shared/add-option'
import { useRequest } from '../shared/useRequest'
import { ReferenceSelect } from '../shared/reference'
import { orNull } from '../shared/text'
import { useInlineAdd } from './useInlineAdd'

//: Location kinds not made by hand -- the auction code makes a `consigned`
//: one and `sold` is reserved -- which the server refuses from a picker.
const MADE_ELSEWHERE = new Set(['consigned', 'sold'])

const BLANK_DRAFT = { kind: '', institution: '', identifier: '' }

/**
 * Where an item is kept: a select over the storage locations, "--" for not
 * recorded, opening with "+ Add a location..." for an inline kind / place / box
 * form -- the vendor and seller pickers' shape.
 *
 * `value` is the location's id as text ('' for none) and `onChange` receives
 * the same; the form turns it into a number or null when it sends it. A list
 * that fails to load leaves only "--" and the add, with the failure said
 * beside them.
 *
 * Where a location is required -- where items come back to from an
 * auction -- the caller names the blank option for what it asks
 * (`blankLabel`, "Choose a location") and refuses it itself. `label` is the
 * select's accessible name when the field around it is not a plain
 * `<label>` naming it.
 */
export default function LocationSelect({
  value,
  onChange,
  disabled = false,
  blankLabel = '--',
  label,
}) {
  const loaded = useRequest('locations', () => api.listStorageLocations())
  const [added, setAdded] = useState([])
  //: True once Add location was pressed before a kind was chosen.
  const [held, setHeld] = useState(false)
  const addOption = useAddOption()
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
        {/* Kept live: pressed without a kind, it says that one is needed. */}
        <button type="button" onClick={() => (ready ? add() : setHeld(true))}>
          Add location
        </button>
        <button type="button" className="link" onClick={() => setAdding(false)}>
          Cancel
        </button>
        {held && !ready && <span className="error">Choose a kind first.</span>}
        {error && <span className="error">{error}</span>}
      </div>
    )
  }

  return (
    <>
      <select
        value={value ?? ''}
        disabled={disabled}
        aria-label={label}
        onKeyDown={addOption.onKeyDown}
        onChange={(e) => {
          if (e.target.value !== ADD) {
            onChange(e.target.value)
            return
          }
          // An arrow steps over Add; only choosing it opens the form.
          const meant = addOption.steppedOver(e.target)
          if (meant === undefined) setAdding(true)
          else if (meant !== null) onChange(meant)
        }}
      >
        <option value="">{blankLabel}</option>
        <option value={ADD}>+ Add a location...</option>
        {locations.map((loc) => (
          <option key={loc.id} value={String(loc.id)}>
            {loc.label}
          </option>
        ))}
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
