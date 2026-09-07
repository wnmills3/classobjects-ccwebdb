import { useCallback, useContext, useState } from 'react'

import { api } from './api'
import { ReferenceContext, useReference } from './reference-context'

/**
 * Classifier vocabularies, fetched once and shared.
 *
 * The API takes classifier *codes* -- `usd_coin_1_00`, `MS64`, `US` -- which is
 * the right contract between machines and useless to a person filling in a
 * form. This turns them into pickers that submit the code and show the label.
 *
 * Cached in one place because a single admin form needs five or six of these
 * vocabularies, and they change about as often as the software does.
 */

export function ReferenceProvider({ children }) {
  const [tables, setTables] = useState({})
  const [pending, setPending] = useState({})

  // After adding a value the cached vocabulary is stale, so it is dropped and
  // refetched rather than patched locally -- the server decides sort order and
  // provenance, and guessing at them here is how a cache starts lying.
  const invalidate = useCallback((table) => {
    setTables((t) => {
      const next = { ...t }
      delete next[table]
      return next
    })
  }, [])

  const load = useCallback(
    async (table) => {
      if (tables[table] || pending[table]) return
      setPending((p) => ({ ...p, [table]: true }))
      try {
        const body = await api.getReference(table)
        setTables((t) => ({ ...t, [table]: body.values }))
      } catch {
        // A missing vocabulary must not take the form down: the field falls
        // back to a free-text input, which is what it was before.
        setTables((t) => ({ ...t, [table]: [] }))
      } finally {
        setPending((p) => ({ ...p, [table]: false }))
      }
    },
    [tables, pending],
  )

  return (
    <ReferenceContext.Provider value={{ tables, load, invalidate }}>
      {children}
    </ReferenceContext.Provider>
  )
}

/**
 * A dropdown over one vocabulary.
 *
 * Falls back to a plain text input while the vocabulary is loading or if it
 * could not be fetched, so the form is always usable -- degraded, never
 * broken.
 */
export function ReferenceSelect({
  table,
  value,
  onChange,
  allowBlank = true,
  placeholder,
}) {
  const values = useReference(table)
  const context = useContext(ReferenceContext)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({ code: '', label: '' })
  const [error, setError] = useState('')

  async function addValue() {
    try {
      await api.addReferenceValue(table, draft)
      context?.invalidate(table)
      onChange({ target: { value: draft.code } })
      setAdding(false)
      setDraft({ code: '', label: '' })
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }

  if (adding) {
    // Vocabularies grow with use: rather than abandoning an entry that does
    // not fit, the missing value is added here and selected immediately.
    return (
      <div className="add-reference">
        <input
          placeholder="code"
          value={draft.code}
          onChange={(e) => setDraft({ ...draft, code: e.target.value })}
        />
        <input
          placeholder="label"
          value={draft.label}
          onChange={(e) => setDraft({ ...draft, label: e.target.value })}
        />
        <button type="button" onClick={addValue} disabled={!draft.code || !draft.label}>
          Add
        </button>
        <button type="button" className="link" onClick={() => setAdding(false)}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    )
  }

  if (!values || values.length === 0) {
    return (
      <input
        value={value ?? ''}
        onChange={onChange}
        placeholder={placeholder}
        aria-label={table}
      />
    )
  }

  return (
    <div className="reference-select">
      <select
        value={value ?? ''}
        onChange={(e) => {
          if (e.target.value === '__add__') setAdding(true)
          else onChange(e)
        }}
        aria-label={table}
      >
        {allowBlank && <option value="">--</option>}
        {values.map((entry) => (
          <option key={entry.code} value={entry.code}>
            {entry.label}
            {/* Values an import invented are marked, so a curated vocabulary
                can be told apart from one collection's guesses. */}
            {entry.source === 'seeded' ? '' : ' *'}
          </option>
        ))}
        <option value="__add__">+ Add a new value...</option>
      </select>
    </div>
  )
}
