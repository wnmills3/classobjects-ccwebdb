/**
 * The shape of a Friedberg number, checked as one is typed.
 *
 * The same rule as `backend/app/fr_format.py`, which is the one that holds:
 * the server cleans and refuses too. This copy says what is wrong before
 * Save is pressed -- a pasted `3007-` for `3007-L`, a stray space, a `Fr. `
 * prefix. Only the form is known here, never which number belongs to which
 * note (CLAUDE.md, *Reference data*).
 */

// `Fr.`, `Fr#`, `FR-`, `Fr. #` -- a label, not part of the number -- taken
// off only where digits follow.
const PREFIX = /^fr[\s.#-]*(?=\d)/i
const SPACED_HYPHEN = /\s*-\s*/g
// 1 to 4 digits, an optional letter, a district -A to -L, * for a star note.
const FORM = /^(\d+)[A-Za-z]?(?:-[A-L])?\*?$/

/** The number as it is kept: trimmed, unprefixed, its district in capitals. */
export function normalizeFr(raw) {
  const number = raw.trim().replace(PREFIX, '').replace(SPACED_HYPHEN, '-')
  const hyphen = number.indexOf('-')
  if (hyphen === -1) return number
  return number.slice(0, hyphen + 1) + number.slice(hyphen + 1).toUpperCase()
}

/** Why `number` (already normalized) is not a Friedberg number, or null. */
export function frProblem(number) {
  if (!number) return 'A Friedberg number is needed.'
  if (number.endsWith('-')) {
    return `${number} ends with a hyphen: its district letter is missing.`
  }
  const match = FORM.exec(number)
  if (!match) {
    return (
      `${number} is not in the form of a Friedberg number: digits, then an ` +
      'optional letter, then -A to -L for a district, then * for a star note.'
    )
  }
  if (match[1].length > 4) {
    return `${number} has ${match[1].length} digits; a Friedberg number has 1 to 4.`
  }
  return null
}
