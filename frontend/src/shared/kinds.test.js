import { describe, expect, it } from 'vitest'

import {
  COIN_ONLY_FIELDS,
  CURRENCY_ONLY_FIELDS,
  fieldFitsKind,
  fitsKind,
  isCurrencyKind,
  sideFor,
} from './kinds'

const entry = (extra) => ({ code: 'x', label: 'X', extra })

describe('fitsKind', () => {
  it('offers an applies_to=currency value only to a note', () => {
    expect(fitsKind(entry({ applies_to: 'currency' }), 'currency')).toBe(true)
    expect(fitsKind(entry({ applies_to: 'currency' }), 'coin')).toBe(false)
  })

  it('offers an applies_to=any value to both', () => {
    expect(fitsKind(entry({ applies_to: 'any' }), 'coin')).toBe(true)
    expect(fitsKind(entry({ applies_to: 'any' }), 'currency')).toBe(true)
  })

  // denomination.kind says `note`, not `currency`: the same face value exists
  // as both a coin and a bill and they are different objects.
  it('maps a denomination kind of note to the currency side', () => {
    expect(fitsKind(entry({ kind: 'note' }), 'currency')).toBe(true)
    expect(fitsKind(entry({ kind: 'note' }), 'coin')).toBe(false)
    expect(fitsKind(entry({ kind: 'coin' }), 'coin')).toBe(true)
  })

  it('treats bullion, sets, medals and tokens as the coin side', () => {
    for (const kind of ['bullion', 'set', 'medal', 'token']) {
      expect(fitsKind(entry({ applies_to: 'coin' }), kind)).toBe(true)
      expect(fitsKind(entry({ kind: 'note' }), kind)).toBe(false)
    }
  })

  it('offers an unmarked value to everything', () => {
    expect(fitsKind(entry({}), 'currency')).toBe(true)
    expect(fitsKind({ code: 'x', label: 'X' }, 'coin')).toBe(true)
  })

  it('names the fields a note does not have', () => {
    expect(COIN_ONLY_FIELDS.has('metal')).toBe(true)
    expect(COIN_ONLY_FIELDS.has('strike_type')).toBe(true)
    expect(isCurrencyKind('currency')).toBe(true)
    expect(isCurrencyKind('bullion')).toBe(false)
  })
})

describe('sideFor', () => {
  it('puts a note on the currency side and everything else on the coin side', () => {
    expect(sideFor('currency')).toBe('currency')
    for (const kind of ['coin', 'bullion', 'set', 'medal', 'token']) {
      expect(sideFor(kind)).toBe('coin')
    }
  })

  // The guarantee this exists for: what a picker MARKS a value it adds with
  // has to be what that picker would then OFFER, or the value vanishes from
  // the picker that created it the moment it appears.
  it('marks a value so that fitsKind then offers it for the same item', () => {
    for (const kind of ['currency', 'coin', 'bullion', 'set', 'medal', 'token']) {
      const added = entry({ applies_to: sideFor(kind) })
      expect(fitsKind(added, kind)).toBe(true)
    }
  })

  // An unknown kind is not a neutral one: it reads as the coin side, which is
  // why every caller is expected to have the item's kind in hand first.
  it('reads an unknown kind as the coin side', () => {
    expect(sideFor(null)).toBe('coin')
    expect(sideFor(undefined)).toBe('coin')
  })
})

describe('fieldFitsKind', () => {
  it("keeps a coin's own fields off a note's form", () => {
    for (const field of COIN_ONLY_FIELDS) {
      expect(fieldFitsKind(field, 'currency')).toBe(false)
      expect(fieldFitsKind(field, 'coin')).toBe(true)
      expect(fieldFitsKind(field, 'bullion')).toBe(true)
    }
  })

  it("keeps a note's own fields off a coin's form", () => {
    for (const field of CURRENCY_ONLY_FIELDS) {
      expect(fieldFitsKind(field, 'currency')).toBe(true)
      expect(fieldFitsKind(field, 'coin')).toBe(false)
      expect(fieldFitsKind(field, 'medal')).toBe(false)
    }
  })

  it('shows a field that belongs to neither side on both forms', () => {
    for (const field of ['grade', 'country', 'denomination', 'status']) {
      expect(fieldFitsKind(field, 'currency')).toBe(true)
      expect(fieldFitsKind(field, 'coin')).toBe(true)
    }
  })

  // The two sets must not overlap: a field in both would be shown or hidden
  // by whichever test ran first, which is not a rule anyone could read.
  it('names no field on both sides', () => {
    for (const field of COIN_ONLY_FIELDS) {
      expect(CURRENCY_ONLY_FIELDS.has(field)).toBe(false)
    }
  })
})
