import { describe, expect, it } from 'vitest'

import { fromCents, isMoney, toCents, totalCents } from './cents'

describe('cents', () => {
  it('reads money text without floating point', () => {
    expect(toCents('189')).toBe(18900)
    expect(toCents('189.5')).toBe(18950)
    expect(toCents('0.07')).toBe(7)
  })

  it('writes cents back as two places', () => {
    expect(fromCents(18950)).toBe('189.50')
    expect(fromCents(7)).toBe('0.07')
  })

  it('totals exactly where floats would not', () => {
    // 19.99 * 3 in floating point is 59.97000000000001.
    expect(totalCents([{ quantity: '3', unit_price: '19.99' }])).toBe(5997)
  })

  it('knows money text from anything else', () => {
    expect(isMoney('150.00')).toBe(true)
    expect(isMoney('150')).toBe(true)
    expect(isMoney('1.234')).toBe(false)
    expect(isMoney('-1')).toBe(false)
    expect(isMoney('')).toBe(false)
  })

  it('leaves out a line that is not yet valid', () => {
    expect(totalCents([{ quantity: '', unit_price: '5.00' }])).toBe(0)
  })
})
