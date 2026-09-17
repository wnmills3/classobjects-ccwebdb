import { useContext, useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import { ReferenceContext } from '../../shared/reference-context'
import { findEntries } from '../../shared/reference-match'

/**
 * The classifier vocabularies: their values' names, and other names for them.
 *
 * A value can be renamed -- the label is what people read; the code, which
 * saved searches and the data use, never changes -- and retired, which takes
 * it out of the pickers while every record that uses it stays as it is. A
 * value the application looks up by its code (a status, a strike, "single")
 * can be renamed but not retired. A renamed value keeps its wording through
 * later seed loads.
 *
 * The standard term stays the label -- DCAM, United States Note -- and what
 * people actually write becomes an alias: UCAM, Legal Tender. Search, the
 * importer and the pickers all recognise an alias the moment it is added.
 *
 * Removing a shipped alias retires it, so the next seed load does not bring
 * it back; it stays listed, struck through, and can be restored. One added
 * here is simply deleted. Two values may share an alias ("Cartwheel" is any
 * large silver dollar): search finds both, but the importer, which cannot
 * choose, uses neither -- so a shared alias is marked.
 */

//: Opened first: the vocabularies whose aliases matter most for search.
const FIRST_TABLE = 'series'

/** How many values in the table carry each alias, by lower-cased alias. */
function aliasCounts(values) {
  const counts = new Map()
  for (const value of values) {
    for (const alias of value.aliases) {
      const key = alias.toLowerCase()
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
  }
  return counts
}

function ValueRow({ table, value, shared, onChanged }) {
  const [draft, setDraft] = useState('')
  // The label being typed, or null when not renaming.
  const [label, setLabel] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function run(call) {
    setSaving(true)
    try {
      onChanged(await call())
      setError('')
      return true
    } catch (err) {
      setError(err.message)
      return false
    } finally {
      setSaving(false)
    }
  }

  async function add(alias) {
    if (await run(() => api.addReferenceAlias(table, value.code, alias))) setDraft('')
  }

  // The label always travels: the endpoint renames and retires in one call.
  const update = (changes) =>
    run(() =>
      api.renameReferenceValue(table, value.code, { label: value.label, ...changes }),
    )

  async function rename() {
    if (await update({ label })) setLabel(null)
  }

  return (
    <tr className={value.is_active ? undefined : 'muted'}>
      <td>
        {label === null ? (
          <>
            {value.label}
            {value.is_active ? '' : ' (retired)'}
            <div className="value-actions">
              <button
                type="button"
                className="link"
                aria-label={`Rename ${value.label}`}
                onClick={() => setLabel(value.label)}
              >
                Rename
              </button>
              {value.is_active ? (
                <button
                  type="button"
                  className="link"
                  aria-label={`Retire ${value.label}`}
                  disabled={saving || value.retirable === false}
                  title={
                    value.retirable === false
                      ? 'The application looks this value up by its code'
                      : 'Stop offering it; records that use it keep it'
                  }
                  onClick={() => update({ is_active: false })}
                >
                  Retire
                </button>
              ) : (
                <button
                  type="button"
                  className="link"
                  aria-label={`Restore ${value.label}`}
                  disabled={saving}
                  onClick={() => update({ is_active: true })}
                >
                  Restore
                </button>
              )}
            </div>
          </>
        ) : (
          <form
            className="alias-add"
            onSubmit={(e) => {
              e.preventDefault()
              if (label.trim()) rename()
            }}
          >
            <input
              value={label}
              aria-label={`New name for ${value.label}`}
              maxLength={255}
              autoFocus
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setLabel(null)
              }}
            />
            <button type="submit" disabled={saving || !label.trim()}>
              Save
            </button>
            <button type="button" className="link" onClick={() => setLabel(null)}>
              Cancel
            </button>
          </form>
        )}
      </td>
      <td>
        <code>{value.code}</code>
      </td>
      <td>
        <div className="alias-list">
          {value.aliases.map((alias) => {
            const isShared = shared(alias)
            return (
              <span
                key={alias}
                className={
                  isShared ? 'chip alias-chip alias-shared' : 'chip alias-chip'
                }
                title={isShared ? 'Another value has this alias too' : undefined}
              >
                {alias}
                {isShared && <span className="muted"> (shared)</span>}
                <button
                  type="button"
                  aria-label={`Remove ${alias} from ${value.label}`}
                  disabled={saving}
                  onClick={() =>
                    run(() => api.removeReferenceAlias(table, value.code, alias))
                  }
                >
                  ×
                </button>
              </span>
            )
          })}
          {value.retired_aliases.map((alias) => (
            <button
              key={alias}
              type="button"
              className="chip alias-retired"
              title="Removed; click to restore"
              aria-label={`Restore ${alias} to ${value.label}`}
              disabled={saving}
              onClick={() => add(alias)}
            >
              {alias}
            </button>
          ))}
          <form
            className="alias-add"
            onSubmit={(e) => {
              e.preventDefault()
              if (draft.trim()) add(draft)
            }}
          >
            <input
              value={draft}
              placeholder="another name"
              aria-label={`New alias for ${value.label}`}
              maxLength={64}
              onChange={(e) => setDraft(e.target.value)}
            />
            <button type="submit" disabled={saving || !draft.trim()}>
              Add
            </button>
          </form>
        </div>
        {error && <p className="error">{error}</p>}
      </td>
    </tr>
  )
}

export default function Vocabularies() {
  const context = useContext(ReferenceContext)
  const [tables, setTables] = useState([])
  const [table, setTable] = useState(FIRST_TABLE)
  // Tagged with their table, so a switch shows "Loading..." rather than the
  // last table's values until the new ones arrive.
  const [loaded, setLoaded] = useState({ table: null, values: null, error: '' })
  const [find, setFind] = useState('')
  const [tablesError, setTablesError] = useState('')
  const values = loaded.table === table ? loaded.values : null
  const error = tablesError || (loaded.table === table ? loaded.error : '')

  useEffect(() => {
    api
      .listReferenceTables()
      .then(setTables)
      .catch((err) => setTablesError(err.message))
  }, [])

  useEffect(() => {
    let current = true
    api
      .getReferenceForEditing(table)
      .then((body) => {
        if (current) setLoaded({ table, values: body.values, error: '' })
      })
      .catch((err) => {
        if (current) setLoaded({ table, values: null, error: err.message })
      })
    return () => {
      current = false
    }
  }, [table])

  const counts = useMemo(() => aliasCounts(values ?? []), [values])

  function changed(updated) {
    setLoaded((l) => ({
      ...l,
      values: l.values.map((v) => (v.code === updated.code ? updated : v)),
    }))
    // Pickers elsewhere hold this vocabulary; their copy is now stale.
    context?.invalidate(table)
  }

  const shown = values ? findEntries(values, find).map(({ entry }) => entry) : []

  return (
    <section>
      <h1>Vocabularies</h1>
      <p className="muted">
        Rename a value, retire one that should no longer be offered, and give values
        other names. Search, the importer and the pickers all recognise an alias. A
        shared alias still finds every value in a search, but the importer will not
        guess between them. Retiring leaves every record that uses the value as it is.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          Vocabulary{/* */}
          <select value={table} onChange={(e) => setTable(e.target.value)}>
            {(tables.length ? tables : [table]).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Find{/* */}
          <input
            type="search"
            value={find}
            placeholder="label, code or alias"
            onChange={(e) => setFind(e.target.value)}
          />
        </label>
      </div>
      {values === null ? (
        !error && <p className="muted">Loading...</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Code</th>
              <th>Aliases</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((value) => (
              <ValueRow
                key={value.code}
                table={table}
                value={value}
                shared={(alias) => (counts.get(alias.toLowerCase()) ?? 0) > 1}
                onChanged={changed}
              />
            ))}
          </tbody>
        </table>
      )}
      {values !== null && find && shown.length === 0 && (
        <p className="muted">
          Nothing in {table} matches {find}.
        </p>
      )}
    </section>
  )
}
