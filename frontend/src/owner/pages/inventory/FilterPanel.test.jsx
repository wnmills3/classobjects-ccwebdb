import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FilterPanel from './FilterPanel'

const config = {
  facetFilters: [['Kind', 'kind', 'item_kind']],
  textFilters: [['Series', 'series', 'e.g. morgan']],
  issueChecks: ['missing_grade', 'missing_cost'],
}

function setup(overrides = {}) {
  const props = {
    config,
    current: {},
    apply: vi.fn(),
    facets: { item_kind: [{ value: 'coin', count: 12 }] },
    issues: { missing_grade: 3 },
    issueDescriptions: { missing_grade: 'No grade recorded' },
    total: 7591,
    busy: false,
    clear: vi.fn(),
    ...overrides,
  }
  return { props, ...render(<FilterPanel {...props} />) }
}

describe('FilterPanel', () => {
  it('renders the search box and the configured filters', () => {
    setup()
    expect(
      screen.getByPlaceholderText(/search descriptions and series/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/kind/i)).toBeInTheDocument()
  })

  it('shows an issue check with its code spelled out and its count', () => {
    setup()
    // The underscore-separated code is rendered with spaces, so the button
    // reads as words rather than an identifier.
    expect(
      screen.getByRole('button', { name: /missing grade \(3\)/i }),
    ).toBeInTheDocument()
  })

  it('applies a search term when the box loses focus', async () => {
    const user = userEvent.setup()
    const { props } = setup()
    const box = screen.getByPlaceholderText(/search descriptions and series/i)
    await user.type(box, 'morgan')
    await user.tab()
    expect(props.apply).toHaveBeenCalledWith({ q: 'morgan' })
  })

  it('toggles an issue filter off when it is already the active one', async () => {
    const user = userEvent.setup()
    const { props } = setup({ current: { issue: 'missing_grade' } })
    await user.click(screen.getByRole('button', { name: /missing grade/i }))
    expect(props.apply).toHaveBeenCalledWith({ issue: '' })
  })
})
