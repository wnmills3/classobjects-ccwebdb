import { describe, expect, it } from 'vitest'

import { fromOzt, toOzt } from './weights'

describe('toOzt', () => {
  it.each([
    ['1', 'ozt', '1'],
    ['0.5', 'ozt', '0.5'],
    ['.25', 'ozt', '.25'],
    // One gram is 0.032151 troy ounces, to the six places the column keeps.
    ['1', 'g', '0.032151'],
    ['31.1034768', 'g', '1.000000'],
    ['10', 'g', '0.321507'],
    [' 5 ', 'g', '0.160754'],
  ])('saves %s %s as %s troy ounces', (text, unit, saved) => {
    expect(toOzt(text, unit)).toBe(saved)
  })

  it('clears the weight when the box is emptied', () => {
    expect(toOzt('', 'g')).toBeNull()
    expect(toOzt('   ', 'ozt')).toBeNull()
  })

  it('hands back what is not a number, for the save to refuse', () => {
    expect(toOzt('1 oz', 'ozt')).toBe('1 oz')
    expect(toOzt('abc', 'g')).toBe('abc')
  })
})

describe('fromOzt', () => {
  it.each([
    ['1.000000', 'ozt', '1'],
    ['0.072340', 'ozt', '0.07234'],
    ['1.000000', 'g', '31.103'],
    ['0.032151', 'g', '1'],
    ['0.321507', 'g', '10'],
    [null, 'g', ''],
    ['', 'ozt', ''],
  ])('shows %s troy ounces in %s as %s', (stored, unit, shown) => {
    expect(fromOzt(stored, unit)).toBe(shown)
  })
})
