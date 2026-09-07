import { createContext, useContext, useEffect } from 'react'

/**
 * The vocabulary context and the hook components use to read one.
 *
 * Separate from the provider and the picker so those two stay a
 * components-only module; a mixed module defeats Fast Refresh.
 */
export const ReferenceContext = createContext(null)

/**
 * One vocabulary, fetched on first use.
 *
 * Returns undefined until it has loaded, so a caller can render a plain text
 * input meanwhile rather than an empty dropdown.
 */
export function useReference(table) {
  const context = useContext(ReferenceContext)
  const { tables, load } = context ?? { tables: {}, load: () => {} }

  useEffect(() => {
    if (table) load(table)
  }, [table, load])

  return tables[table]
}
