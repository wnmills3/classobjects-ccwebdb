// Money arrives from the API as a decimal string (NUMERIC in Postgres).
// Format for display only -- never do arithmetic on the formatted value; sum
// in cents (`shared/cents.js`) and format the result.

const DECIMAL = /^-?\d+(\.\d+)?$/

//: One formatter per currency, built on first use.
const formatters = new Map()

function formatterFor(currencyCode) {
  if (!formatters.has(currencyCode)) {
    formatters.set(
      currencyCode,
      new Intl.NumberFormat('en-US', { style: 'currency', currency: currencyCode }),
    )
  }
  return formatters.get(currencyCode)
}

/**
 * A money amount as a person reads it: `money('1234.5')` is `$1,234.50`.
 *
 * The decimal string is handed to `Intl.NumberFormat` as a string, which
 * formats it exactly rather than through a JavaScript number -- so a figure
 * too precise for a float is never rounded on the way to the screen.
 * Anything that is not a plain decimal renders as `--`, not as `$NaN`.
 *
 * `currencyCode` is the ISO code the amount is in. Listings and catalogue
 * items carry one (`currency`); pass it where the data has it. USD is only
 * the default for shapes that carry none -- orders today. A code `Intl`
 * does not recognise is shown as written beside the amount.
 */
export function money(value, currencyCode = 'USD') {
  const text = typeof value === 'number' ? String(value) : String(value ?? '').trim()
  if (!DECIMAL.test(text)) return '--'
  const code = currencyCode || 'USD'
  try {
    return formatterFor(code).format(text)
  } catch {
    // Not an ISO 4217 code -- currencies are an admin-edited vocabulary, and
    // Intl refuses anything it does not know. The amount and the code as
    // stored, rather than a page that fails to render.
    return `${text} ${code}`
  }
}

export function date(value) {
  if (!value) return ''
  return new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
