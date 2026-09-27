import { isCurrencyKind } from '../shared/kinds'

/**
 * The fields that identify a piece of this kind, in the order they are
 * written: "Series 1934-A $5" and its serial and plates for a note, "1921-D
 * $1" for anything else. Entry starts with these, and most other classifiers
 * follow from them (`docs/specs/identify-first-entry-design.md`).
 *
 * Mint is not asked of the kind: every kind but a note has one.
 */
export function identifyKeys(kind) {
  return isCurrencyKind(kind)
    ? [
        'series_year',
        'series_letter',
        'denomination',
        'serial_number',
        'face_plate_number',
        'back_plate_number',
      ]
    : ['year_start', 'mint', 'denomination']
}
