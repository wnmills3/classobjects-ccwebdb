import { createContext, useContext, useEffect, useMemo } from 'react'

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
 *
 * The provider holds retired values too, because a record may still use
 * one and its picker has to show it. They are left out unless a caller asks
 * for them with `includeRetired`: nothing should offer a retired value.
 */
export function useReference(table, { includeRetired = false } = {}) {
  const context = useContext(ReferenceContext)
  const { tables, load } = context ?? { tables: {}, load: () => {} }

  useEffect(() => {
    if (table) load(table)
  }, [table, load])

  const values = tables[table]
  return useMemo(
    () =>
      values && !includeRetired
        ? values.filter((entry) => entry.is_active !== false)
        : values,
    [values, includeRetired],
  )
}
