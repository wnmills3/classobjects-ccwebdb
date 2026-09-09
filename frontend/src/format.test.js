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

  it('treats null and empty string as zero, not as absent', () => {
    // Documenting real behaviour rather than the intuitive one: Number(null)
    // and Number('') are both 0, which is finite, so these format as $0.00
    // while undefined and a non-numeric string give '--'. Callers that can
    // receive a null price must guard before calling, the way
    // InventoryTable's cell() does; the other call sites rely on the API
    // never sending one.
    expect(money(null)).toBe('$0.00')
    expect(money('')).toBe('$0.00')
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
