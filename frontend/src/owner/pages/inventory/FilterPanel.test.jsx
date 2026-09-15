import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FilterPanel from './FilterPanel'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

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

  it('shows a value by its label but filters by its code', async () => {
    // A denomination's code is `usd_coin_0_01`, which nobody would pick from
    // a list; its label is "Cent". The filter still compares the code.
    const user = userEvent.setup()
    const { props } = setup({
      config: {
        ...config,
        facetFilters: [['Denomination', 'denomination', 'denomination']],
      },
      facets: { denomination: [{ value: 'usd_coin_0_01', label: 'Cent', count: 353 }] },
    })
    const select = screen.getByRole('combobox', { name: /denomination/i })
    expect(screen.getByRole('option', { name: 'Cent (353)' })).toBeInTheDocument()

    await user.selectOptions(select, 'usd_coin_0_01')
    expect(props.apply).toHaveBeenCalledWith({ denomination: 'usd_coin_0_01' })
  })

  it('falls back to the value when a facet has no label', () => {
    setup()
    expect(screen.getByRole('option', { name: 'coin (12)' })).toBeInTheDocument()
  })

  it('offers a denomination choice in both inventories', () => {
    for (const view of [COIN_VIEW, CURRENCY_VIEW]) {
      expect(view.facetFilters, view.view).toContainEqual([
        'Denomination',
        'denomination',
        'denomination',
      ])
    }
  })
})
