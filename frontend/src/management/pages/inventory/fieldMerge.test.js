import { describe, expect, it } from 'vitest'

import { baseFor, conflictsOf, fieldValue, rebase, sameValue } from './fieldMerge'

describe('sameValue', () => {
  it('treats blank and null alike', () => {
    expect(sameValue('', null)).toBe(true)
    expect(sameValue(undefined, '')).toBe(true)
    expect(sameValue('', 'x')).toBe(false)
  })

  it('compares a numeric field as numbers, as app.field_changes does', () => {
    const numeric = { numeric: true }
    expect(sameValue('84', '84.00', numeric)).toBe(true)
    expect(sameValue(1881, '1881', numeric)).toBe(true)
    expect(sameValue('84.00', '84.01', numeric)).toBe(false)
  })

  it('compares any other field as text: an identifier 0153 is not 153', () => {
    expect(sameValue('0153', '153')).toBe(false)
    expect(sameValue('84', '84.00')).toBe(false)
    expect(sameValue(1881, '1881')).toBe(true)
  })

  it('compares attribute lists as sets', () => {
    expect(sameValue(['a', 'b'], ['b', 'a'])).toBe(true)
    expect(sameValue(['a'], ['a', 'b'])).toBe(false)
  })

  it('compares lists as sets whatever order either arrives in', () => {
    // Certificate numbers and codes of mixed case and length, where an
    // ordering by number, by locale or by case would each sort differently.
    const held = ['10', '9', 'B2', 'a1', 'CAC', '0091']
    for (let turn = 0; turn < held.length; turn += 1) {
      const turned = [...held.slice(turn), ...held.slice(0, turn)]
      expect(sameValue(held, turned)).toBe(true)
      expect(sameValue([...turned].reverse(), held)).toBe(true)
    }
    expect(sameValue(held, [...held.slice(1), '09'])).toBe(false)
    expect(sameValue([10, 9], ['9', '10'])).toBe(true)
  })
})

describe('merging a change made elsewhere', () => {
  const opened = { version: 1, description: 'as bought', source_title: 'Morgan' }

  it('flags only an edited field that someone else changed', () => {
    const draft = { description: 'cleaned' }
    const theirsElsewhere = { ...opened, version: 2, source_title: 'Morgan $1' }
    expect(conflictsOf(theirsElsewhere, opened, draft)).toEqual([])

    const theirsHere = { ...opened, version: 2, description: 'original skin' }
    expect(conflictsOf(theirsHere, opened, draft)).toEqual(['description'])
  })

  it('flags a change to a text field that only a number comparison would miss', () => {
    // The server refuses this save (its fields other than NUMERIC_FIELDS are
    // text), so the form must show the conflict for a choice to be made.
    const draft = { back_plate_number: '160' }
    const base = { ...opened, back_plate_number: '0153' }
    const theirs = { ...base, version: 2, back_plate_number: '153' }
    expect(conflictsOf(theirs, base, draft)).toEqual(['back_plate_number'])
  })

  it('compares a cost as a number, so 84 against 84.00 is no conflict', () => {
    const draft = { item_cost: '90.00' }
    const base = { ...opened, item_cost: '84.00' }
    expect(conflictsOf({ ...base, version: 2, item_cost: '84' }, base, draft)).toEqual(
      [],
    )
  })

  it('does not flag the same change made twice', () => {
    const draft = { description: 'cleaned' }
    const same = { ...opened, version: 2, description: 'cleaned' }
    expect(conflictsOf(same, opened, draft)).toEqual([])
  })

  it('keeps an edited field’s base across a refresh, and takes the rest', () => {
    const draft = { description: 'cleaned' }
    const fresh = { ...opened, version: 2, description: 'theirs', source_title: 'new' }
    const base = rebase(fresh, opened, draft)
    expect(base.description).toBe('as bought')
    expect(base.source_title).toBe('new')
    expect(base.version).toBe(2)
    expect(baseFor(base, draft)).toEqual({ description: 'as bought' })
  })

  it('reads attributes as codes', () => {
    expect(fieldValue({ attributes: [{ code: 'cac' }] }, 'attributes')).toEqual(['cac'])
  })
})
