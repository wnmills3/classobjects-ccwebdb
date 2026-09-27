import { describe, expect, it } from 'vitest'

import { identifyKeys } from './identify'

describe('identifyKeys', () => {
  it('asks a note for its series, face value and serial, then its plates', () => {
    expect(identifyKeys('currency')).toEqual([
      'series_year',
      'series_letter',
      'denomination',
      'serial_number',
      'face_plate_number',
      'back_plate_number',
    ])
  })

  it('asks anything else for its year, mint and face value', () => {
    for (const kind of ['coin', 'bullion', 'medal', 'set']) {
      expect(identifyKeys(kind)).toEqual(['year_start', 'mint', 'denomination'])
    }
  })
})
