import { describe, expect, it } from 'vitest'

import { orNull } from './text'

describe('orNull', () => {
  it('sends typed text trimmed', () => {
    expect(orNull('  1881-S  ')).toBe('1881-S')
  })

  it('sends a blank box as null, not as an empty string', () => {
    expect(orNull('')).toBeNull()
    expect(orNull('   ')).toBeNull()
  })

  it('treats an absent value as blank', () => {
    expect(orNull(null)).toBeNull()
    expect(orNull(undefined)).toBeNull()
  })

  it('sends a number as its text', () => {
    expect(orNull(12)).toBe('12')
    expect(orNull(0)).toBe('0')
  })
})
