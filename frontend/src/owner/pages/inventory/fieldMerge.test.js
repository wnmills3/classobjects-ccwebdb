import { describe, expect, it } from 'vitest'

import { baseFor, conflictsOf, fieldValue, rebase, sameValue } from './fieldMerge'

describe('sameValue', () => {
  it('treats blank and null alike', () => {
    expect(sameValue('', null)).toBe(true)
    expect(sameValue(undefined, '')).toBe(true)
    expect(sameValue('', 'x')).toBe(false)
  })

  it('compares numbers as numbers, the way the server does', () => {
    expect(sameValue('84', '84.00')).toBe(true)
    expect(sameValue(1881, '1881')).toBe(true)
    expect(sameValue('84.00', '84.01')).toBe(false)
  })

  it('compares attribute lists as sets', () => {
    expect(sameValue(['a', 'b'], ['b', 'a'])).toBe(true)
    expect(sameValue(['a'], ['a', 'b'])).toBe(false)
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
