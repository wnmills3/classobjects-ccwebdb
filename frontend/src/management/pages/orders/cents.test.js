import { describe, expect, it } from 'vitest'

import { totalCents } from './cents'

describe('totalCents', () => {
  it('totals exactly where floats would not', () => {
    // 0.1 * 3 * 100 in floating point is 30.000000000000004, and
    // 1.15 * 100 is 114.99999999999999.
    expect(totalCents([{ quantity: '3', unit_price: '0.10' }])).toBe(30)
    expect(totalCents([{ quantity: '1', unit_price: '1.15' }])).toBe(115)
  })

  it('leaves out a line that is not yet valid', () => {
    expect(totalCents([{ quantity: '', unit_price: '5.00' }])).toBe(0)
  })

  it('excludes a line with an invalid price instead of throwing', () => {
    expect(totalCents([{ quantity: '3', unit_price: '1.239' }])).toBe(0)
  })
})
