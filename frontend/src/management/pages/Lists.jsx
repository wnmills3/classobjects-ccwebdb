import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { frProblem, normalizeFr } from '../friedberg-format'
import { withScheme } from '../site-name'
import { useReference } from '../../shared/reference-context'

/**
 * The owner-kept lists that are not vocabularies -- the Friedberg catalog,
 * sellers, vendors and storage locations -- listed, added to, corrected
 * and pruned (`docs/specs/list-maintenance-design.md`).
 *
 * Each grows mostly by inline entry while something else is being done,
 * and this page is where a slip made there is corrected -- a Friedberg
 * number pasted as `9907-` for `9907-L` -- and where one is added ahead of
 * the purchase or the item that will use it. One table serves
 * all four, driven by a small description of each (`lists` below): its
 * columns, which of them can be edited, how it is read and written, and how
 * many records use a row -- only a row nothing uses offers Delete, and the
 * server refuses the rest anyway (every foreign key into these is
 * RESTRICT).
 *
 * The tab is in the URL (`?tab=sellers`), so a reload or a link lands on it.
 */

// Not made by hand: the auction code makes a `consigned` location and
// `sold` is reserved -- see `routers/acquisitions.py` -- so neither is
// offered as a kind nor edited.
const MADE_ELSEWHERE = new Set(['consigned', 'sold'])

const TABS = [
  { key: 'friedberg', label: 'Friedberg numbers' },
  { key: 'sellers', label: 'Sellers' },
  { key: 'vendors', label: 'Vendors' },
  { key: 'locations', label: 'Storage locations' },
]

//: A Friedberg number, cleaned and checked as one typed at a lookup is
//: (`friedberg-format.js`): neither a correction nor a new row may bring a
//: slip of its own.
const FR_NUMBER = {
  key: 'fr_number',
  label: 'Number',
  clean: normalizeFr,
  check: frProblem,
  required: true,
}
const FR_DESCRIPTION = { key: 'description', label: 'Description' }

/**
 * A link as the server takes it, on an add and on a correction alike: it
 * accepts only an http(s) address, and `ebay.com` is meant as one
 * (`withScheme`). '' for nothing typed, as `clean` gives every blank.
 */
function webLink(text) {
  return withScheme(text) ?? ''
}

/** The label `options` gives `code`, or the code itself when it has none. */
function labelIn(options, code) {
  return options.find((entry) => entry.code === code)?.label ?? code ?? ''
}

/** A Friedberg row's type, as recorded: what the lookup matched it by. */
function typeOf(row) {
  const series = [row.series_year, row.series_letter].filter(Boolean).join('')
  return [
    row.denomination,
    row.note_type,
    series && `series ${series}`,
    row.district_letter && `district ${row.district_letter}`,
    row.seal_color && `${row.seal_color} seal`,
    row.web_press && 'web press',
    row.printing_facility?.toUpperCase(),
  ]
    .filter(Boolean)
    .join(', ')
}

/**
 * The description of each list, by tab key, that `CatalogTable` is driven
 * by: how it is read, added to, changed and deleted, what a row is called,
 * how many records use it, the text a search matches, its columns -- a
 * column with a `field` can be edited -- and `adds`, the fields a new row
 * is asked for. A field `required` refuses a blank. The two kind
 * vocabularies are passed in
 * because their labels and pickers come from reference data loaded later.
 */
function lists(vendorKinds, locationKinds) {
  // Every kind names a row; only the ones made by hand are offered.
  const offeredKinds = locationKinds.filter((entry) => !MADE_ELSEWHERE.has(entry.code))
  const sellerName = { key: 'name', label: 'Name', required: true }
  const storeLink = { key: 'store_url', label: 'Store link', clean: webLink }
  const vendorName = { key: 'name', label: 'Name', required: true }
  const vendorLink = { key: 'url', label: 'Link', clean: webLink }
  const vendorKind = { key: 'vendor_kind', label: 'Kind', options: vendorKinds }
  const locationKind = {
    key: 'kind',
    label: 'Kind',
    options: offeredKinds,
    required: true,
  }
  const institution = { key: 'institution', label: 'Institution' }
  const identifier = { key: 'identifier', label: 'Identifier' }
  const notes = { key: 'notes', label: 'Notes' }
  return {
    friedberg: {
      noun: 'number',
      load: api.listFriedbergCatalog,
      // A number alone is a row the server takes: the note's type is
      // learnt when a note in hand is matched to it.
      create: api.createFriedbergNumber,
      adds: [FR_NUMBER, FR_DESCRIPTION],
      update: api.updateFriedbergNumber,
      remove: api.deleteFriedbergNumber,
      name: (row) => row.fr_number,
      uses: (row) => row.item_count,
      usesLabel: 'Items',
      searchText: (row) =>
        [row.fr_number, row.description, typeOf(row)].filter(Boolean).join(' '),
      columns: [
        {
          label: 'Number',
          value: (row) => <span className="mono">{row.fr_number}</span>,
          field: FR_NUMBER,
        },
        { label: 'Type', value: typeOf },
        {
          label: 'Description',
          value: (row) => row.description ?? '',
          field: FR_DESCRIPTION,
        },
        { label: 'Status', value: (row) => (row.verified ? 'confirmed' : 'proposed') },
      ],
      // Confirming is what lets the next lookup attach this number in one
      // step (FriedbergLookup's Use); undoing it sends it back to proposed.
      actions: (row, change) =>
        row.verified ? (
          <button type="button" onClick={() => change(row, { verified: false })}>
            Undo confirm
          </button>
        ) : (
          <button type="button" onClick={() => change(row, { verified: true })}>
            Confirm
          </button>
        ),
    },
    sellers: {
      noun: 'seller',
      load: api.listSellers,
      create: api.createSeller,
      adds: [sellerName, storeLink],
      update: api.updateSeller,
      remove: api.deleteSeller,
      name: (row) => row.name,
      uses: (row) => row.order_count,
      usesLabel: 'Purchases',
      searchText: (row) => [row.name, row.store_url].filter(Boolean).join(' '),
      columns: [
        {
          label: 'Name',
          value: (row) => row.name,
          field: sellerName,
        },
        {
          label: 'Store',
          value: (row) => row.store_url ?? '',
          field: storeLink,
        },
      ],
    },
    vendors: {
      noun: 'vendor',
      load: api.listVendors,
      create: api.createVendor,
      adds: [vendorName, vendorLink, vendorKind],
      update: api.updateVendor,
      remove: api.deleteVendor,
      name: (row) => row.name,
      uses: (row) => row.order_count,
      usesLabel: 'Purchases and platforms',
      searchText: (row) => [row.name, row.url].filter(Boolean).join(' '),
      columns: [
        {
          label: 'Name',
          value: (row) => row.name,
          field: vendorName,
        },
        {
          label: 'Link',
          value: (row) => row.url ?? '',
          field: vendorLink,
        },
        {
          label: 'Kind',
          value: (row) => labelIn(vendorKinds, row.vendor_kind),
          field: vendorKind,
        },
      ],
    },
    locations: {
      noun: 'location',
      load: api.listStorageLocations,
      create: api.createStorageLocation,
      adds: [locationKind, institution, identifier, notes],
      update: api.updateStorageLocation,
      remove: api.deleteStorageLocation,
      name: (row) => row.label,
      uses: (row) => row.item_count,
      // Items kept there now and moves recorded to it: one item can be both.
      usesLabel: 'Item records',
      editable: (row) => !MADE_ELSEWHERE.has(row.kind),
      searchText: (row) =>
        [row.label, row.institution, row.identifier, row.notes]
          .filter(Boolean)
          .join(' '),
      columns: [
        {
          label: 'Kind',
          value: (row) => labelIn(locationKinds, row.kind),
          field: locationKind,
        },
        {
          label: 'Institution',
          value: (row) => row.institution ?? '',
          field: institution,
        },
        {
          label: 'Identifier',
          value: (row) => row.identifier ?? '',
          field: identifier,
        },
        {
          label: 'Notes',
          value: (row) => row.notes ?? '',
          field: notes,
        },
      ],
    },
  }
}

/**
 * The Lists page: a tab per list and the table for the one chosen. A tab
 * the address does not name, or names wrongly, is the Friedberg catalog.
 */
export default function Lists() {
  const [params, setParams] = useSearchParams()
  const wanted = params.get('tab')
  const tab = TABS.some((entry) => entry.key === wanted) ? wanted : 'friedberg'
  const vendorKinds = useReference('vendor_kind') ?? []
  const locationKinds = useReference('storage_location_kind') ?? []
  const config = lists(vendorKinds, locationKinds)[tab]

  return (
    <div className="lists">
      <h2>Lists</h2>
      <div className="tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="tab"
            aria-selected={entry.key === tab}
            onClick={() => setParams({ tab: entry.key })}
          >
            {entry.label}
          </button>
        ))}
      </div>
      <CatalogTable key={tab} config={config} />
    </div>
  )
}

/**
 * Why `value`, typed and cleaned for `field`, cannot be saved, or nothing
 * when it can: the field's own check, and a required field left blank.
 */
function refusalOf(field, value) {
  const problem = field.check?.(value)
  if (problem) return problem
  // A name sent as null is left as it was by the server, which would read
  // here as a save that took; a new row without one is refused.
  if (value === '' && field.required) return `${field.label} cannot be blank.`
  return null
}

/**
 * One catalog's rows: search, add, edit in place, and delete what nothing
 * uses.
 *
 * An edit sends only the fields changed, blank as null; a new row sends the
 * fields filled in. The list is read again after every write -- what is
 * shown is what the server kept.
 */
function CatalogTable({ config }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState({})
  const [deleting, setDeleting] = useState(null)
  //: The new row being typed, by field key; null while no add is open.
  const [adding, setAdding] = useState(null)
  const { load } = config

  const reload = useCallback(() => {
    load()
      .then((body) => setRows(body))
      .catch((err) => {
        setError(err.message)
        setRows((previous) => previous ?? [])
      })
  }, [load])

  useEffect(() => reload(), [reload])

  async function write(action) {
    setError('')
    try {
      await action()
      setEditing(null)
      setDraft({})
      setDeleting(null)
      setAdding(null)
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  function change(row, body) {
    return write(() => config.update(row.id, body))
  }

  function cancelEdit() {
    setEditing(null)
    setDraft({})
  }

  function save(row) {
    const body = {}
    for (const column of config.columns) {
      const { field } = column
      const key = field?.key
      if (!key || !(key in draft)) continue
      const value = field.clean ? field.clean(draft[key]) : draft[key].trim()
      const problem = refusalOf(field, value)
      if (problem) {
        setError(problem)
        return
      }
      if (value !== (row[key] ?? '')) body[key] = value === '' ? null : value
    }
    if (Object.keys(body).length === 0) {
      setEditing(null)
      return
    }
    change(row, body)
  }

  function add(e) {
    e.preventDefault()
    const body = {}
    for (const field of config.adds) {
      const typed = adding[field.key] ?? ''
      const value = field.clean ? field.clean(typed) : typed.trim()
      const problem = refusalOf(field, value)
      if (problem) {
        setError(problem)
        return
      }
      if (value !== '') body[field.key] = value
    }
    write(() => config.create(body))
  }

  if (rows === null) return <p className="muted">Loading...</p>

  const firstEditable = config.columns.find((column) => column.field)
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? rows.filter((row) => config.searchText(row).toLowerCase().includes(needle))
    : rows

  return (
    <div className="catalog-table">
      <label className="search-row">
        Search{/* */}
        <input
          type="search"
          aria-label="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {adding === null ? (
        <button
          type="button"
          onClick={() => {
            setAdding({})
            setEditing(null)
            setDeleting(null)
            setError('')
          }}
        >
          Add {config.noun}
        </button>
      ) : (
        <form className="row" aria-label={`New ${config.noun}`} onSubmit={add}>
          {config.adds.map((field) => (
            <label key={field.key}>
              {field.label}
              {/* */}
              <Field
                field={field}
                value={adding[field.key] ?? ''}
                onChange={(value) => setAdding({ ...adding, [field.key]: value })}
              />
            </label>
          ))}
          <button type="submit">Add</button>
          <button
            type="button"
            className="link"
            onClick={() => {
              setAdding(null)
              setError('')
            }}
          >
            Cancel
          </button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      {shown.length === 0 ? (
        <p className="muted">
          {needle ? `Nothing matches ${search.trim()}.` : 'Nothing here yet.'}
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              {config.columns.map((column) => (
                <th key={column.label}>{column.label}</th>
              ))}
              <th>{config.usesLabel}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const isEditing = editing === row.id
              const editable = config.editable ? config.editable(row) : true
              const uses = config.uses(row)
              return (
                <tr key={row.id}>
                  {config.columns.map((column) => (
                    <td key={column.label}>
                      {isEditing && column.field ? (
                        <Field
                          field={column.field}
                          value={draft[column.field.key] ?? row[column.field.key] ?? ''}
                          onChange={(value) =>
                            setDraft({ ...draft, [column.field.key]: value })
                          }
                          // Edit opens the row ready to type in; Enter in any
                          // of its boxes is its Save button, Escape its Cancel.
                          autoFocus={column === firstEditable}
                          onEnter={() => save(row)}
                          onEscape={cancelEdit}
                        />
                      ) : (
                        column.value(row)
                      )}
                    </td>
                  ))}
                  <td>{uses}</td>
                  <td className="row">
                    {isEditing ? (
                      <>
                        <button type="button" onClick={() => save(row)}>
                          Save
                        </button>
                        <button type="button" className="link" onClick={cancelEdit}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {deleting === row.id && (
                          <>
                            <span>Delete {config.name(row)}?</span>
                            <button
                              type="button"
                              onClick={() => write(() => config.remove(row.id))}
                            >
                              Yes, delete
                            </button>
                            <button
                              type="button"
                              className="link"
                              onClick={() => setDeleting(null)}
                            >
                              No
                            </button>
                          </>
                        )}
                        {deleting !== row.id && editable && (
                          <>
                            <button
                              type="button"
                              onClick={() => {
                                setEditing(row.id)
                                setDraft({})
                                setDeleting(null)
                                setAdding(null)
                              }}
                            >
                              Edit
                            </button>
                            {config.actions?.(row, change)}
                            {uses === 0 && (
                              <button type="button" onClick={() => setDeleting(row.id)}>
                                Delete
                              </button>
                            )}
                          </>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

/**
 * A text box, or a picker when the field has options.
 *
 * A value the options do not hold -- none at all, or a retired one -- is
 * offered as itself, first: a picker with no option for its value shows its
 * first option, which nobody chose and which a save would not send.
 *
 * `onEnter` and `onEscape`, when given, are called for those keys pressed
 * in it: a table row has no form to submit, so the row being edited saves
 * and cancels this way.
 */
function Field({ field, value, onChange, autoFocus = false, onEnter, onEscape }) {
  const keys = { Enter: onEnter, Escape: onEscape }
  const onKeyDown =
    onEnter || onEscape
      ? (e) => {
          const handler = keys[e.key]
          if (!handler) return
          e.preventDefault()
          handler()
        }
      : undefined
  if (field.options) {
    const offered = field.options.some((option) => option.code === value)
    return (
      <select
        aria-label={field.label}
        value={value}
        autoFocus={autoFocus}
        onKeyDown={onKeyDown}
        onChange={(e) => onChange(e.target.value)}
      >
        {!offered && <option value={value}>{value === '' ? '--' : value}</option>}
        {field.options.map((option) => (
          <option key={option.code} value={option.code}>
            {option.label}
          </option>
        ))}
      </select>
    )
  }
  return (
    <input
      type="text"
      aria-label={field.label}
      value={value}
      autoFocus={autoFocus}
      onKeyDown={onKeyDown}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
