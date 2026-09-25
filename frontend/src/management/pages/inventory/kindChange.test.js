import { describe, expect, it } from 'vitest'

import { clearedByKind } from './kindChange'

const vocab = {
  denomination: [
    { code: 'usd_coin_1_00', extra: { kind: 'coin' } },
    { code: 'usd_note_1', extra: { kind: 'note' } },
  ],
  series: [
    { code: 'peace_dollar', extra: { applies_to: 'coin' } },
    { code: 'national_bank_note_1929', extra: { applies_to: 'currency' } },
  ],
  grade: [
    { code: '65', extra: { grade_scale: 'sheldon' } },
    { code: 'N64', extra: { grade_scale: 'note' } },
  ],
  item_attribute: [
    { code: 'first_strike', extra: { applies_to: 'coin' } },
    { code: 'star_note', extra: { applies_to: 'currency' } },
    { code: 'error', extra: { applies_to: 'any' } },
  ],
}

const from = (values) => (key) => values[key]

describe('clearedByKind', () => {
  it("empties a coin's metal, strike, weights, series, denomination and grade for a note", () => {
    const coin = from({
      metal: 'silver',
      strike_type: 'business',
      fine_weight_ozt: '0.773440',
      series: 'peace_dollar',
      denomination: 'usd_coin_1_00',
      grade: '65',
      attributes: ['first_strike', 'error'],
    })

    const { fields, attributes } = clearedByKind('currency', coin, vocab)

    expect(fields).toEqual({
      metal: 'silver',
      strike_type: 'business',
      fine_weight_ozt: '0.773440',
      series: 'peace_dollar',
      denomination: 'usd_coin_1_00',
      grade: '65',
    })
    expect(attributes).toEqual({ keep: ['error'], dropped: ['first_strike'] })
  })

  it("empties a note's own fields for a coin, its serial above all", () => {
    const note = from({
      serial_number: 'F06566560R',
      series_year: 1999,
      seal_color: 'green',
      denomination: 'usd_note_1',
      grade: 'N64',
      attributes: ['star_note'],
    })

    const { fields, attributes } = clearedByKind('coin', note, vocab)

    expect(fields).toEqual({
      serial_number: 'F06566560R',
      series_year: 1999,
      seal_color: 'green',
      denomination: 'usd_note_1',
      grade: 'N64',
    })
    expect(attributes).toEqual({ keep: [], dropped: ['star_note'] })
  })

  it('gives up nothing between kinds on the same side', () => {
    const coin = from({ metal: 'silver', denomination: 'usd_coin_1_00', grade: '65' })

    expect(clearedByKind('set', coin, vocab)).toEqual({ fields: {}, attributes: null })
  })

  it('keeps a value whose vocabulary has not loaded, for the server to name', () => {
    const coin = from({ denomination: 'usd_coin_1_00' })

    expect(clearedByKind('currency', coin, {}).fields).toEqual({})
  })
})
