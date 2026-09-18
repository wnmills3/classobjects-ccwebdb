import { describe, expect, it } from 'vitest'

import { COIN_ONLY_FIELDS, fitsKind, isCurrencyKind } from './kinds'

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
