import { describe, expect, it } from 'vitest'

import { serialDistrict } from './serialDistrict'

const frn = (serial_number) => ({ note_type: 'frn', serial_number })

describe('serialDistrict', () => {
  it('reads the letter next to the digits: the only one, or the second of two', () => {
    expect(serialDistrict(frn('G28888882E'))).toBe('G')
    expect(serialDistrict(frn('MK12345678A'))).toBe('K')
    expect(serialDistrict(frn('B12345678*'))).toBe('B')
  })

  it('reads it as typed: any case, spaces, and a digit too few or too many', () => {
    expect(serialDistrict(frn(' g 2888 8882 e '))).toBe('G')
    expect(serialDistrict(frn('K4888444D'))).toBe('K')
    expect(serialDistrict(frn('G288888882E'))).toBe('G')
  })

  it('reads none where the serial names no Bank', () => {
    // A star for the prefix, a letter past L, three letters, no serial.
    expect(serialDistrict(frn('*12345678A'))).toBe('')
    expect(serialDistrict(frn('M12345678A'))).toBe('')
    expect(serialDistrict(frn('GOLD999999'))).toBe('')
    expect(serialDistrict(frn('323256EL'))).toBe('')
    expect(serialDistrict(frn(''))).toBe('')
    expect(serialDistrict(frn(null))).toBe('')
    expect(serialDistrict(undefined)).toBe('')
  })

  it('reads none from a note that is not a Federal Reserve Note', () => {
    // A silver certificate's first letter is a block, not a Bank.
    expect(
      serialDistrict({ note_type: 'silver_certificate', serial_number: 'B12345678A' }),
    ).toBe('')
    expect(serialDistrict({ note_type: null, serial_number: 'B12345678A' })).toBe('')
  })
})
