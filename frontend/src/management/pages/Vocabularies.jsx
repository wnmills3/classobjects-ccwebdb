import { useContext, useEffect, useMemo, useRef, useState } from 'react'

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
 * Merge into... replaces a value with another for good: every item holding it
 * moves to the value kept, its names become that value's aliases, and it is
 * removed. The page shows what would move before it asks.
 *
 * The standard term stays the label -- UCAM, United States Note -- and what
 * people actually write becomes an alias: Ultra Cameo, Legal Tender. Search and the
 * pickers recognise an alias the moment it is added.
 *
 * Removing a shipped alias retires it, so the next seed load does not bring
 * it back; it stays listed, struck through, and can be restored. One added
 * here is simply deleted. Two values may share an alias ("Cartwheel" is any
 * large silver dollar): search finds both, and a shared alias is marked.
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

function describe(result, value, target) {
  const items = `${result.items} item${result.items === 1 ? '' : 's'}`
  const dropped = result.dropped
    ? ` (${result.dropped} already had ${target.label} and keep that instead)`
    : ''
  const names = result.aliases.length
    ? ` ${result.aliases.join(', ')} will find ${target.label}.`
    : ''
  return (
    `Moves ${items} to ${target.label}${dropped}, then removes ` +
    `${value.label}.${names} This cannot be undone.`
  )
}

function MergePanel({ table, value, others, onMerged, onCancel }) {
  const [into, setInto] = useState('')
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // The choice a preview answers; a slower answer for an earlier one is dropped.
  const latest = useRef('')
  const target = others.find((o) => o.code === into)

  async function choose(code) {
    latest.current = code
    setInto(code)
    setPreview(null)
    setError('')
    if (!code) return
    try {
      const result = await api.mergeReferenceValue(table, value.code, code, true)
      if (latest.current === code) setPreview(result)
    } catch (err) {
      if (latest.current === code) setError(err.message)
    }
  }

  async function merge() {
    setBusy(true)
    try {
      onMerged(
        await api.mergeReferenceValue(table, value.code, into, false, {
          // The preview above named them; confirming is the acknowledgement.
          acknowledgeForSale: (preview?.for_sale_count ?? 0) > 0,
        }),
      )
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="merge-panel">
      <label>
        Merge into{/* */}
        <select
          value={into}
          aria-label={`Merge ${value.label} into`}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">--</option>
          {others.map((other) => (
            <option key={other.code} value={other.code}>
              {other.label}
            </option>
          ))}
        </select>
      </label>
      {preview && target && <p>{describe(preview, value, target)}</p>}
      {preview?.for_sale_count > 0 && (
        <p className="for-sale" role="alert">
          <strong>
            {preview.for_sale_count} of them{' '}
            {preview.for_sale_count === 1 ? 'is' : 'are'} for sale
          </strong>
          : {preview.for_sale.join(', ')}
          {preview.for_sale_count > preview.for_sale.length && ', and others'}. Merging
          changes what a buyer is looking at.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="button" disabled={!preview || busy} onClick={merge}>
          {busy ? 'Merging...' : 'Merge'}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function ValueRow({ table, value, others, shared, sequenced, onChanged, onMerged }) {
  const [draft, setDraft] = useState('')
  // The position being typed, as text; the value's own until it is edited.
  const [order, setOrder] = useState(String(value.sort_order))
  // The label being typed, or null when not renaming.
  const [label, setLabel] = useState(null)
  const [merging, setMerging] = useState(false)
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
              <button
                type="button"
                className="link"
                aria-label={`Merge ${value.label} into another value`}
                disabled={saving || value.retirable === false}
                title={
                  value.retirable === false
                    ? 'The application looks this value up by its code'
                    : 'Move its items to another value, then remove it'
                }
                onClick={() => setMerging(true)}
              >
                Merge into...
              </button>
            </div>
            {merging && (
              <MergePanel
                table={table}
                value={value}
                others={others}
                onMerged={onMerged}
                onCancel={() => setMerging(false)}
              />
            )}
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
      {sequenced && (
        <td>
          <form
            className="alias-add"
            onSubmit={(e) => {
              e.preventDefault()
              update({ sort_order: Number(order) })
            }}
          >
            {/* Text with a digits pattern, not type=number: a number input
                steps on the mouse wheel, which reorders a vocabulary by
                scrolling the page. */}
            <input
              className="order-input"
              inputMode="numeric"
              pattern="[0-9]+"
              aria-label={`Position of ${value.label}`}
              value={order}
              onChange={(e) => setOrder(e.target.value)}
            />
            <button
              type="submit"
              disabled={
                saving || !/^\d+$/.test(order) || Number(order) === value.sort_order
              }
            >
              Move
            </button>
          </form>
        </td>
      )}
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
  const [reloads, setReloads] = useState(0)
  const [notice, setNotice] = useState('')
  const values = loaded.table === table ? loaded.values : null
  const sequenced = loaded.table === table && loaded.sequenced
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
        if (current)
          setLoaded({
            table,
            values: body.values,
            sequenced: Boolean(body.sequenced),
            error: '',
          })
      })
      .catch((err) => {
        if (current) setLoaded({ table, values: null, error: err.message })
      })
    return () => {
      current = false
    }
  }, [table, reloads])

  const counts = useMemo(() => aliasCounts(values ?? []), [values])

  function changed(updated) {
    setLoaded((l) => {
      const next = l.values.map((v) => (v.code === updated.code ? updated : v))
      // A moved value goes where it now sorts, the way the server orders a
      // sequenced table: by position, then code.
      if (l.sequenced) {
        next.sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code))
      }
      return { ...l, values: next }
    })
    // Pickers elsewhere hold this vocabulary; their copy is now stale.
    context?.invalidate(table)
  }

  function merged(result) {
    setNotice(
      `Merged ${result.code} into ${result.into}: ${result.items} ` +
        `item${result.items === 1 ? '' : 's'} moved.`,
    )
    setReloads((n) => n + 1)
    context?.invalidate(table)
  }

  const shown = values ? findEntries(values, find).map(({ entry }) => entry) : []
  const active = (values ?? []).filter((v) => v.is_active)

  return (
    <section>
      <h1>Vocabularies</h1>
      <p className="muted">
        Rename a value, retire one that should no longer be offered, and give values
        other names. Search and the pickers both recognise an alias, and a shared alias
        finds every value that has it. Retiring leaves every record that uses the value
        as it is.
      </p>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}
      <div className="filter-grid">
        <label>
          Vocabulary{/* */}
          <select
            value={table}
            onChange={(e) => {
              setTable(e.target.value)
              setNotice('')
            }}
          >
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
              {sequenced && <th>Position</th>}
              <th>Code</th>
              <th>Aliases</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((value) => (
              <ValueRow
                // The position is part of the key so the Position field starts
                // again from the stored value whenever that changes -- a move,
                // a reload -- rather than keeping what was last typed.
                key={`${value.code}:${value.sort_order}`}
                table={table}
                value={value}
                others={active.filter((v) => v.code !== value.code)}
                shared={(alias) => (counts.get(alias.toLowerCase()) ?? 0) > 1}
                sequenced={sequenced}
                onChanged={changed}
                onMerged={merged}
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
