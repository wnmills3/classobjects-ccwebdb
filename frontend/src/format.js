// Money arrives from the API as a decimal string (NUMERIC in Postgres).
// Format for display only -- never do arithmetic on the formatted value.
const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

export function money(value) {
  const n = Number(value)
  return Number.isFinite(n) ? currency.format(n) : '--'
}

export function date(value) {
  if (!value) return ''
  return new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
