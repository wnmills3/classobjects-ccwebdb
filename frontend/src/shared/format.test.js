import { describe, expect, it } from 'vitest'

import { date, money } from './format'

describe('money', () => {
  it('formats a decimal string as US currency', () => {
    expect(money('1234.5')).toBe('$1,234.50')
  })

  it('formats zero rather than treating it as absent', () => {
    expect(money('0')).toBe('$0.00')
  })

  it('returns a dash for a value that is not a number', () => {
    // The API sends NUMERIC as a string, so a malformed one must not render
    // as "NaN" in the middle of a price column.
    expect(money('not a price')).toBe('--')
    expect(money(undefined)).toBe('--')
  })

  it('treats null and empty string as absent, not as zero', () => {
    // Changed 2026-09-22 (was $0.00, because Number(null) is 0). "Nothing
    // recorded" and "free" are different facts, the distinction the console
    // keeps everywhere else, so an absent amount renders as absent.
    expect(money(null)).toBe('--')
    expect(money('')).toBe('--')
  })

  it('formats a decimal string exactly, never through a float', () => {
    // 0.1 + 0.2 territory: a string with more precision than a double holds
    // must not come out rounded to a different cent.
    expect(money('9007199254740993.10')).toBe('$9,007,199,254,740,993.10')
  })

  it('formats in the currency it is given', () => {
    expect(money('12.5', 'EUR')).toBe('€12.50')
    expect(money('12.5', 'CAD')).toBe('CA$12.50')
  })

  it('falls back to US dollars when no currency is known', () => {
    expect(money('12.5', undefined)).toBe('$12.50')
    expect(money('12.5', null)).toBe('$12.50')
  })
})

describe('date', () => {
  it('returns an empty string for a missing value', () => {
    expect(date(null)).toBe('')
    expect(date('')).toBe('')
    expect(date(undefined)).toBe('')
  })

  it('renders a date in a readable form', () => {
    // Asserting the parts rather than an exact string: the format follows the
    // runtime's locale, and pinning it would make this test machine-specific.
    const rendered = date('2026-03-14T00:00:00Z')
    expect(rendered).toMatch(/2026/)
    expect(rendered).not.toBe('')
  })
})
