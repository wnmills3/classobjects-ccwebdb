import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import { api } from './api'

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
const ReferenceContext = createContext(null)

export function ReferenceProvider({ children }) {
  const [tables, setTables] = useState({})
  const [pending, setPending] = useState({})

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
    <ReferenceContext.Provider value={{ tables, load }}>
      {children}
    </ReferenceContext.Provider>
  )
}

export function useReference(table) {
  const context = useContext(ReferenceContext)
  const { tables, load } = context ?? { tables: {}, load: () => {} }

  useEffect(() => {
    if (table) load(table)
  }, [table, load])

  return tables[table]
}

/**
 * A dropdown over one vocabulary.
 *
 * Falls back to a plain text input while the vocabulary is loading or if it
 * could not be fetched, so the form is always usable -- degraded, never
 * broken.
 */
export function ReferenceSelect({ table, value, onChange, allowBlank = true, placeholder }) {
  const values = useReference(table)

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
    <select value={value ?? ''} onChange={onChange} aria-label={table}>
      {allowBlank && <option value="">--</option>}
      {values.map((entry) => (
        <option key={entry.code} value={entry.code}>
          {entry.label}
          {/* Values an import invented are marked, so a curated vocabulary
              can be told apart from one collection's guesses. */}
          {entry.source === 'derived' ? ' *' : ''}
        </option>
      ))}
    </select>
  )
}
