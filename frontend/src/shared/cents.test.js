import { describe, expect, it } from 'vitest'

import { centsOrZero, fromCents, isMoney, signedFromCents, toCents } from './cents'

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

  it('knows money text from anything else', () => {
    expect(isMoney('150.00')).toBe(true)
    expect(isMoney('150')).toBe(true)
    expect(isMoney('1.234')).toBe(false)
    expect(isMoney('-1')).toBe(false)
    expect(isMoney('')).toBe(false)
  })

  it('refuses to read text that is not money', () => {
    expect(() => toCents('1.')).toThrow(RangeError)
    expect(() => toCents('')).toThrow(RangeError)
    expect(() => toCents('-5')).toThrow(RangeError)
    expect(() => toCents('1.239')).toThrow(RangeError)
    expect(() => toCents('1e5')).toThrow(RangeError)
  })
})

describe('signedFromCents', () => {
  it('writes a loss with its sign', () => {
    // fromCents(-250) is "-3.50": Math.floor rounds away from zero below it.
    expect(signedFromCents(-250)).toBe('-2.50')
    expect(signedFromCents(-7)).toBe('-0.07')
  })

  it('writes zero and a gain as fromCents does', () => {
    expect(signedFromCents(0)).toBe('0.00')
    expect(signedFromCents(18950)).toBe('189.50')
  })
})

describe('centsOrZero', () => {
  it('reads an amount, spaces and all', () => {
    expect(centsOrZero('12.50')).toBe(1250)
    expect(centsOrZero(' 3 ')).toBe(300)
  })

  it('counts nothing for a blank, absent or half-typed amount', () => {
    expect(centsOrZero('')).toBe(0)
    expect(centsOrZero(null)).toBe(0)
    expect(centsOrZero(undefined)).toBe(0)
    expect(centsOrZero('1.')).toBe(0)
    expect(centsOrZero('abc')).toBe(0)
  })
})
