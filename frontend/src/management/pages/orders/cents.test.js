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
    // 0.1 * 3 * 100 in floating point is 30.000000000000004, and
    // 1.15 * 100 is 114.99999999999999.
    expect(totalCents([{ quantity: '3', unit_price: '0.10' }])).toBe(30)
    expect(totalCents([{ quantity: '1', unit_price: '1.15' }])).toBe(115)
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

  it('refuses to read text that is not money', () => {
    expect(() => toCents('1.')).toThrow(RangeError)
    expect(() => toCents('')).toThrow(RangeError)
    expect(() => toCents('-5')).toThrow(RangeError)
    expect(() => toCents('1.239')).toThrow(RangeError)
    expect(() => toCents('1e5')).toThrow(RangeError)
  })

  it('excludes a line with an invalid price instead of throwing', () => {
    expect(totalCents([{ quantity: '3', unit_price: '1.239' }])).toBe(0)
  })
})
