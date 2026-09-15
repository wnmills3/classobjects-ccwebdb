const title = (row) => row.listing_title ?? `listing ${row.listing_id}`

/** One history row as a phrase. Money values arrive as plain decimals. */
export function describeChange(row) {
  const from = row.from_value
  const to = row.to_value
  switch (row.change) {
    case 'placed':
      return `placed by ${to}`
    case 'line_added':
      return `added ${title(row)}: ${to}`
    case 'line_removed':
      return `removed ${title(row)} (was ${from})`
    case 'quantity':
      return `${title(row)} quantity ${from} -> ${to}`
    case 'unit_price':
      return `${title(row)} price $${from} -> $${to}`
    case 'customer':
      return `customer ${from} -> ${to}`
    case 'notes':
      return 'notes changed'
    case 'status':
      return `status ${from} -> ${to}`
    case 'total':
      return `total $${from} -> $${to}`
    default:
      return row.change
  }
}
