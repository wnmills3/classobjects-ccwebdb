/**
 * The shape of a Friedberg number, checked as one is typed.
 *
 * The same rule as `backend/app/fr_format.py`, which is the one that holds:
 * the server cleans and refuses too. This copy says what is wrong before
 * Save is pressed -- a pasted `9907-` for `9907-L`, a stray space, a `Fr. `
 * prefix. Only the form is known here, never which number belongs to which
 * note (`docs/reference-data.md`).
 */

// `Fr.`, `Fr#`, `FR-`, `Fr. #` -- a label, not part of the number -- taken
// off only where digits follow.
const PREFIX = /^fr[\s.#-]*(?=\d)/i
const SPACED_HYPHEN = /\s*-\s*/g
// 1 to 4 digits, an optional letter, a district -A to -L, m for a mule
// (9907-Em), * for a star note.
// Then, after a space, LGS or DGS for a light or dark green seal (9908-B LGS).
const FORM = /^(\d+)[A-Za-z]?(?:-[A-L]m?)?\*?(?: (?:LGS|DGS))?$/
// A seal shade typed at the end, any case and spacing.
const SHADE = /\s+(lgs|dgs)$/i
// A district with a mule's m, the star typed either side of it: kept as
// Em*, the m lower-case so capitalising the district cannot make it a
// second district letter.
const MULE_DISTRICT = /^([a-l])(\*?)m(\*?)$/i

/** The number as it is kept: trimmed, unprefixed, its district in capitals. */
export function normalizeFr(raw) {
  const trimmed = raw.trim()
  const shade = SHADE.exec(trimmed)
  if (!shade) return normalizeBase(trimmed)
  return `${normalizeBase(trimmed.slice(0, shade.index))} ${shade[1].toUpperCase()}`
}

/** The number before any seal shade, cleaned. */
function normalizeBase(raw) {
  const number = raw.trim().replace(PREFIX, '').replace(SPACED_HYPHEN, '-')
  const hyphen = number.indexOf('-')
  if (hyphen === -1) return number
  const head = number.slice(0, hyphen + 1)
  const district = number.slice(hyphen + 1)
  const mule = MULE_DISTRICT.exec(district)
  if (mule) return `${head}${mule[1].toUpperCase()}m${mule[2] || mule[3]}`
  return head + district.toUpperCase()
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
      'optional letter, then -A to -L for a district, then m for a mule, then * ' +
      'for a star note, then LGS or DGS for a seal shade.'
    )
  }
  if (match[1].length > 4) {
    return `${number} has ${match[1].length} digits; a Friedberg number has 1 to 4.`
  }
  return null
}
