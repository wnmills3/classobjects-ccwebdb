/**
 * Percent <-> fraction conversion for a platform's rates.
 *
 * Split out of `Platforms.jsx` because that module exports the page
 * component; a component module that also exports plain functions breaks
 * Fast Refresh (`react-refresh/only-export-components` -- see
 * `docs/code-quality.md`). Kept here so the page and its test can both
 * import the helpers without pulling in React.
 */

/** "13.25" (percent, as typed) -> "0.1325"; blank -> null. */
export function percentToFraction(text) {
  const trimmed = String(text ?? '').trim()
  if (trimmed === '') return null
  const [whole, frac = ''] = trimmed.split('.')
  // Shift the decimal point two places left without floating-point error.
  const digits = (whole.padStart(3, '0') + frac).replace(/^0+(?=\d{3})/, '')
  const cut = digits.length - frac.length - 2
  const result = `${digits.slice(0, cut) || '0'}.${digits.slice(cut)}`
  return result.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') || '0'
}

/** "0.1325" -> "13.25"; null -> "". */
export function fractionToPercent(value) {
  if (value === null || value === undefined || value === '') return ''
  const [whole, frac = ''] = String(value).split('.')
  const padded = frac.padEnd(2, '0')
  const shifted = `${whole}${padded.slice(0, 2)}`.replace(/^0+(?=\d)/, '')
  const rest = padded.slice(2).replace(/0+$/, '')
  return rest ? `${shifted}.${rest}` : shifted
}
