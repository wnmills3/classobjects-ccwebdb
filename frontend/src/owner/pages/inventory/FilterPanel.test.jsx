import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FilterPanel, { SHARED_KEYS } from './FilterPanel'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

const config = {
  facetFilters: [['Kind', 'kind', 'item_kind']],
  textFilters: [['Series', 'series', 'e.g. morgan']],
  issueChecks: ['missing_grade', 'missing_cost'],
}

/** Open the tips. Their label is split around the underlined letter, which
 * getByText cannot match, so the summary is found by element. */
async function openTips(user) {
  await user.click(document.querySelector('.search-help summary'))
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
    expect(screen.getByRole('textbox', { name: 'Search' })).toBeInTheDocument()
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
    const box = screen.getByRole('textbox', { name: 'Search' })
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
      // The fourth element is the accelerator, checked on its own below.
      expect(
        view.facetFilters.map((f) => f.slice(0, 3)),
        view.view,
      ).toContainEqual(['Denomination', 'denomination', 'denomination'])
    }
  })
})

describe('FilterPanel search tips', () => {
  it('shows no tips for a view that defines none', () => {
    setup()
    expect(document.querySelector('.search-help')).toBeNull()
  })

  it('uses the view placeholder, which points at the tips', () => {
    setup({ config: { ...config, ...COIN_VIEW } })
    expect(screen.getByRole('textbox', { name: 'Search' })).toHaveAttribute(
      'placeholder',
      expect.stringMatching(/tips below/),
    )
  })

  it('explains that several words are one phrase, in order', () => {
    // The behaviour nobody guesses: "morgan 1921" found nothing while
    // "1921 morgan" found 27.
    setup({ config: { ...config, ...COIN_VIEW } })
    expect(screen.getByText(/one phrase, in that order/i)).toBeInTheDocument()
    expect(screen.getByText(/% matches anything in between/i)).toBeInTheDocument()
    expect(screen.getByText(/_ matches exactly one character/i)).toBeInTheDocument()
  })

  it('runs an example when it is clicked, and shows it in the box', async () => {
    // The box is uncontrolled; without writing the term into it, the results
    // would change while the box still showed whatever was typed before.
    const user = userEvent.setup()
    const { props } = setup({
      config: { ...config, ...COIN_VIEW },
      current: { q: 'buffalo' },
    })
    await openTips(user)
    await user.click(screen.getByRole('button', { name: /search for morgan%1921/i }))

    expect(props.apply).toHaveBeenCalledWith({ q: 'morgan%1921' })
    expect(screen.getByRole('textbox', { name: 'Search' })).toHaveValue('morgan%1921')
  })

  it('gives the currency view its own examples, without series names', async () => {
    const user = userEvent.setup()
    setup({ config: { ...config, ...CURRENCY_VIEW } })
    await openTips(user)
    expect(
      screen.getByRole('button', { name: /search for silver certificate/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /search for mercury/i })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Search' })).toHaveAttribute(
      'placeholder',
      expect.not.stringMatching(/series/i),
    )
  })
})

describe('FilterPanel keyboard accelerators', () => {
  /** Every letter a view puts on screen, with the label it underlines. */
  function lettersOf(view) {
    const shared = [
      ['Search', SHARED_KEYS.search],
      ['Search tips', SHARED_KEYS.tips],
      ['Year from', SHARED_KEYS.yearFrom],
      ['Year to', SHARED_KEYS.yearTo],
      ['Clear filters', SHARED_KEYS.clear],
    ]
    const own = [...view.facetFilters, ...view.textFilters].map((f) => [f[0], f[3]])
    return [...shared, ...own]
  }

  for (const view of [COIN_VIEW, CURRENCY_VIEW]) {
    describe(view.title, () => {
      it('gives every field a letter, with no letter used twice', () => {
        const pairs = lettersOf(view)
        for (const [label, letter] of pairs) {
          expect(letter, `${label} has no accelerator`).toMatch(/^[a-z]$/)
        }
        const letters = pairs.map(([, letter]) => letter)
        const repeated = letters.filter((l, i) => letters.indexOf(l) !== i)
        expect(repeated, `letters used twice: ${repeated}`).toEqual([])
      })

      it('avoids D, E and F, which the browser keeps for itself', () => {
        const letters = lettersOf(view).map(([, letter]) => letter)
        expect(letters.filter((l) => 'def'.includes(l))).toEqual([])
      })

      it('uses a letter that appears in its own label, so it can be underlined', () => {
        for (const [label, letter] of lettersOf(view)) {
          expect(label.toLowerCase(), `${label} / ${letter}`).toContain(letter)
        }
      })
    })
  }

  it('marks each field with its access key and announces it', () => {
    const { container } = setup({ config: { ...config, ...COIN_VIEW }, facets: {} })
    const box = screen.getByRole('textbox', { name: 'Search' })
    expect(box).toHaveAttribute('accesskey', 's')
    expect(box).toHaveAttribute('aria-keyshortcuts', 'Alt+S')

    const series = screen.getByRole('combobox', { name: 'Series' })
    expect(series).toHaveAttribute('accesskey', 'r')

    expect(screen.getByRole('textbox', { name: 'Item code' })).toHaveAttribute(
      'accesskey',
      'i',
    )
    expect(container.querySelector('.search-help summary')).toHaveAttribute(
      'accesskey',
      'h',
    )
    expect(screen.getByRole('button', { name: 'Clear filters' })).toHaveAttribute(
      'accesskey',
      'c',
    )
  })

  it('underlines the letter in the label', () => {
    setup({ config: { ...config, ...CURRENCY_VIEW }, facets: {} })
    const seriesYear = screen.getByRole('combobox', { name: 'Series year' })
    const underlined = seriesYear.closest('label').querySelector('u')
    expect(underlined).toHaveTextContent('a')
  })

  it('leaves a filter without a letter plain rather than failing', () => {
    // The panel is also used with hand-built configs, as in these tests.
    setup()
    const kind = screen.getByRole('combobox', { name: 'Kind' })
    expect(kind).not.toHaveAttribute('accesskey')
  })
})
