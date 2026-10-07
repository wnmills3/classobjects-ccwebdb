import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { frProblem, normalizeFr } from '../friedberg-format'
import { useReference } from '../../shared/reference-context'

/**
 * The owner-kept lists that are not vocabularies -- the Friedberg catalog,
 * sellers, vendors and storage locations -- listed, corrected and pruned
 * (`docs/specs/list-maintenance-design.md`).
 *
 * Each grows by inline entry while something else is being done, and this
 * page is where a slip made there is corrected -- a Friedberg number pasted
 * as `3007-` for `3007-L`. One table serves
 * all four, driven by a small description of each (`lists` below): its
 * columns, which of them can be edited, how it is read and written, and how
 * many records use a row -- only a row nothing uses offers Delete, and the
 * server refuses the rest anyway (every foreign key into these is
 * RESTRICT).
 *
 * The tab is in the URL (`?tab=sellers`), so a reload or a link lands on it.
 */

// Made by the auction and sale code, not by hand -- see
// `routers/acquisitions.py` -- so neither offered as a kind nor edited.
const MADE_ELSEWHERE = new Set(['consigned', 'sold'])

const TABS = [
  { key: 'friedberg', label: 'Friedberg numbers' },
  { key: 'sellers', label: 'Sellers' },
  { key: 'vendors', label: 'Vendors' },
  { key: 'locations', label: 'Storage locations' },
]

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
 * by: how it is read, changed and deleted, what a row is called, how many
 * records use it, the text a search matches, and its columns -- a column
 * with a `field` can be edited. The two kind vocabularies are passed in
 * because their labels and pickers come from reference data loaded later.
 */
function lists(vendorKinds, locationKinds) {
  // Every kind names a row; only the ones made by hand are offered.
  const offeredKinds = locationKinds.filter((entry) => !MADE_ELSEWHERE.has(entry.code))
  return {
    friedberg: {
      noun: 'number',
      load: api.listFriedbergCatalog,
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
          // Cleaned and checked as a new number is (`friedberg-format.js`):
          // a correction must not bring a slip of its own.
          field: {
            key: 'fr_number',
            label: 'Number',
            clean: normalizeFr,
            check: frProblem,
          },
        },
        { label: 'Type', value: typeOf },
        {
          label: 'Description',
          value: (row) => row.description ?? '',
          field: { key: 'description', label: 'Description' },
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
          field: { key: 'name', label: 'Name' },
        },
        {
          label: 'Store',
          value: (row) => row.store_url ?? '',
          field: { key: 'store_url', label: 'Store link' },
        },
      ],
    },
    vendors: {
      noun: 'vendor',
      load: api.listVendors,
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
          field: { key: 'name', label: 'Name' },
        },
        {
          label: 'Link',
          value: (row) => row.url ?? '',
          field: { key: 'url', label: 'Link' },
        },
        {
          label: 'Kind',
          value: (row) => labelIn(vendorKinds, row.vendor_kind),
          field: { key: 'vendor_kind', label: 'Kind', options: vendorKinds },
        },
      ],
    },
    locations: {
      noun: 'location',
      load: api.listStorageLocations,
      update: api.updateStorageLocation,
      remove: api.deleteStorageLocation,
      name: (row) => row.label,
      uses: (row) => row.item_count,
      usesLabel: 'Items',
      editable: (row) => !MADE_ELSEWHERE.has(row.kind),
      searchText: (row) =>
        [row.label, row.institution, row.identifier, row.notes]
          .filter(Boolean)
          .join(' '),
      columns: [
        {
          label: 'Kind',
          value: (row) => labelIn(locationKinds, row.kind),
          field: { key: 'kind', label: 'Kind', options: offeredKinds },
        },
        {
          label: 'Institution',
          value: (row) => row.institution ?? '',
          field: { key: 'institution', label: 'Institution' },
        },
        {
          label: 'Identifier',
          value: (row) => row.identifier ?? '',
          field: { key: 'identifier', label: 'Identifier' },
        },
        {
          label: 'Notes',
          value: (row) => row.notes ?? '',
          field: { key: 'notes', label: 'Notes' },
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
 * One catalog's rows: search, edit in place, and delete what nothing uses.
 *
 * An edit sends only the fields changed, blank as null, and the list is read
 * again after every write -- what is shown is what the server kept.
 */
function CatalogTable({ config }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState({})
  const [deleting, setDeleting] = useState(null)
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
      reload()
    } catch (err) {
      setError(err.message)
    }
  }

  function change(row, body) {
    return write(() => config.update(row.id, body))
  }

  function save(row) {
    const body = {}
    for (const column of config.columns) {
      const { field } = column
      const key = field?.key
      if (!key || !(key in draft)) continue
      const value = field.clean ? field.clean(draft[key]) : draft[key].trim()
      const problem = field.check?.(value)
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

  if (rows === null) return <p className="muted">Loading...</p>

  const needle = search.trim().toLowerCase()
  const shown = needle
    ? rows.filter((row) => config.searchText(row).toLowerCase().includes(needle))
    : rows

  return (
    <div className="catalog-table">
      <label className="search-row">
        Search
        <input
          type="search"
          aria-label="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      {shown.length === 0 ? (
        <p className="muted">Nothing here yet.</p>
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
                        <button
                          type="button"
                          className="link"
                          onClick={() => {
                            setEditing(null)
                            setDraft({})
                          }}
                        >
                          Cancel
                        </button>
                      </>
                    ) : deleting === row.id ? (
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
                    ) : (
                      editable && (
                        <>
                          <button
                            type="button"
                            onClick={() => {
                              setEditing(row.id)
                              setDraft({})
                              setDeleting(null)
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
                      )
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

/** A text box, or a picker when the field has options. */
function Field({ field, value, onChange }) {
  if (field.options) {
    return (
      <select
        aria-label={field.label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
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
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
