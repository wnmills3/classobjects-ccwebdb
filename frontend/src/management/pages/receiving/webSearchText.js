// Its own module, apart from FriedbergLookup.jsx: a component module that
// also exports a plain function defeats Fast Refresh.

/**
 * The question to ask Google's AI Mode for this note's Friedberg number.
 *
 * Built from the form as it stands, in the words a dealer's listing uses:
 * "What is the Friedberg number for Series 1963-A $1 Federal Reserve Note
 * New York Granahan Fowler?" -- a question, because AI Mode answers one
 * directly where a keyword list gets pages of results. `labels` maps a
 * field's code to its vocabulary label; a code with no label yet (the
 * vocabulary still loading) is used as it is.
 *
 * The owner reads the result and types the number in. Nothing here fetches
 * or stores what a search finds -- a machine collecting Friedberg numbers
 * is the harvesting CLAUDE.md's reference-data rule forbids.
 */
//: Where it was printed, as a dealer's listing says it.
const PRINTED_AT = { dc: 'printed in Washington DC', fw: 'printed in Fort Worth' }

export function webSearchText(fields, labels = {}) {
  const label = (table, code) => (code ? (labels[table]?.[code] ?? code) : '')
  const series = fields.seriesYear
    ? `Series ${fields.seriesYear}${fields.seriesLetter ? `-${fields.seriesLetter}` : ''}`
    : ''
  const parts = [
    series,
    label('denomination', fields.denomination).replace(/ Bill$/, ''),
    label('note_type', fields.noteType),
    // "B - New York" -> "New York": the city is what listings say.
    label('fed_district', fields.district).replace(/^[A-L] - /, ''),
    label('signature_combination', fields.signatureCombination).replace(' / ', ' '),
    fields.press === 'yes' ? 'web press' : '',
    PRINTED_AT[fields.printing] ?? '',
  ]
  const note = parts.filter(Boolean).join(' ')
  // The plates are how a mule is told apart: a face and back from
  // different eras. The answer's "m" suffix says it is one (owner,
  // 2026-09-25), so the question asks for it.
  const plates = [
    fields.facePlate ? `face plate ${fields.facePlate}` : '',
    fields.backPlate ? `back plate ${fields.backPlate}` : '',
  ].filter(Boolean)
  const described = note || 'this US banknote'
  if (plates.length === 0) return `What is the Friedberg number for ${described}?`
  return (
    `What is the Friedberg number for ${described} with ${plates.join(' and ')}? ` +
    'If it is a mule, give the number with its m suffix.'
  )
}
