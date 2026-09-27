import { describe, expect, it } from 'vitest'

import { identifyChanges, identifyKeys, identifyValues } from './identify'

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

const NOTE = {
  item_kind: 'currency',
  denomination: 'usd_note_1',
  series_year: 1957,
  series_letter: 'B',
  serial_number: 'A1B',
  face_plate_number: null,
  back_plate_number: null,
}

const COIN = {
  item_kind: 'coin',
  year_start: 1921,
  year_end: 1921,
  mint: 'D',
  denomination: 'usd_coin_1_00',
}

describe('identifyValues', () => {
  it("fills a note's facts as the form's text, blank for what is unknown", () => {
    expect(identifyValues(NOTE)).toEqual({
      series_year: '1957',
      series_letter: 'B',
      denomination: 'usd_note_1',
      serial_number: 'A1B',
      face_plate_number: '',
      back_plate_number: '',
    })
  })
})

describe('identifyChanges', () => {
  it('is empty when nothing moved, spaces included', () => {
    const values = { ...identifyValues(NOTE), serial_number: ' A1B ' }
    expect(identifyChanges(NOTE, values)).toEqual({ changes: {}, base: {} })
  })

  it('sends each changed fact with what it was, a year as a number', () => {
    const values = {
      ...identifyValues(NOTE),
      series_year: '1963',
      back_plate_number: '12',
    }
    expect(identifyChanges(NOTE, values)).toEqual({
      changes: { series_year: 1963, back_plate_number: '12' },
      base: { series_year: 1957, back_plate_number: null },
    })
  })

  it('sends a cleared fact as null', () => {
    const values = { ...identifyValues(NOTE), series_letter: '' }
    expect(identifyChanges(NOTE, values).changes).toEqual({ series_letter: null })
  })

  it("moves a coin's single year as both ends", () => {
    const values = { ...identifyValues(COIN), year_start: '1922' }
    expect(identifyChanges(COIN, values)).toEqual({
      changes: { year_start: 1922, year_end: 1922 },
      base: { year_start: 1921, year_end: 1921 },
    })
  })
})
