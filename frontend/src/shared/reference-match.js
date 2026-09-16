/**
 * Finding a vocabulary value by what someone types.
 *
 * A value is found by its label, its code or any of its aliases -- the names
 * people actually use ("Walker", "Legal Tender", "UCAM") for the standard
 * term the label holds. The alias is reported back so a picker can show why
 * a value it offers matched.
 */

function contains(text, needle) {
  return String(text ?? '')
    .toLowerCase()
    .includes(needle)
}

/**
 * How one entry matches the typed text: `{ alias }` naming the alias that
 * matched when the label and code did not, `{ alias: null }` for a match on
 * the label or code, or null for no match. Empty text matches everything.
 */
export function entryMatch(entry, text) {
  const needle = String(text ?? '')
    .trim()
    .toLowerCase()
  if (!needle) return { alias: null }
  if (contains(entry.label, needle) || contains(entry.code, needle)) {
    return { alias: null }
  }
  const alias = (entry.aliases ?? []).find((a) => contains(a, needle))
  return alias ? { alias } : null
}

/** The entries the text finds, in their own order, each with its match. */
export function findEntries(entries, text) {
  return entries
    .map((entry) => ({ entry, match: entryMatch(entry, text) }))
    .filter(({ match }) => match !== null)
}
