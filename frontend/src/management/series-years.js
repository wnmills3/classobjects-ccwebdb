/**
 * A coin's years against its design series' own years.
 *
 * A Morgan dollar dated 1800 is a typo -- the series runs 1878 on -- and it
 * was found only because the description came out without a weight: no
 * composition covers 1800, so the save had nothing to fill (owner,
 * 2026-10-01). Said beside the year, as a notice, never a refusal: the
 * series' years are a fact about the design, and a pattern or restrike can
 * fall outside them.
 */

function yearOf(value) {
  if (value === '' || value === null || value === undefined) return null
  const year = Number(value)
  return Number.isInteger(year) ? year : null
}

/**
 * Why the years `start`..`end` fall outside `series`' years, or '' when they
 * do not -- or when either side is not known. `series` is the reference
 * entry, whose `extra` carries `year_start` and `year_end` (no end: still
 * struck).
 */
export function seriesYearProblem(series, start, end) {
  const from = yearOf(series?.extra?.year_start)
  const first = yearOf(start)
  if (from === null || first === null) return ''
  const to = yearOf(series.extra.year_end)
  const last = yearOf(end) ?? first
  if (first >= from && (to === null || last <= to)) return ''
  const span = to === null ? `${from} on` : `${from}-${to}`
  const years = last === first ? `${first}` : `${first}-${last}`
  return `${series.label} runs ${span}; ${years} is outside it. Check the year.`
}
