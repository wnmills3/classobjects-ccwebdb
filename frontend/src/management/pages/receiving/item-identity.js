// Which item a Receiving result is, beyond its often-repeated title.

/**
 * What tells a result from the others on one order, whose titles are often
 * all the same: a note's serial number, a coin's face value and series.
 * Null when the record holds none of them.
 */
export function identity(row) {
  if (row.view === 'currency') {
    return row.serial_number ? `S/N ${row.serial_number}` : 'no serial number'
  }
  const parts = [row.denomination_label, row.series_label].filter(Boolean)
  return parts.length ? parts.join(', ') : null
}
