import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FIND_FROM, ReferenceSelect } from './reference'
import { entryMatch, findEntries } from './reference-match'
import { emptyReference, renderWithProviders } from '../test/helpers'

function value(code, label, aliases = []) {
  return { code, label, sort_order: 0, source: 'seeded', aliases, extra: {} }
}

const WALKER = value('walking_liberty_half', 'Walking Liberty Half Dollar', ['Walker'])
const MERCURY = value('winged_liberty_head_dime', 'Winged Liberty Head Dime', [
  'Mercury',
  'Merc',
])
// Enough others that the picker counts as long.
const OTHERS = Array.from({ length: FIND_FROM }, (_, n) =>
  value(`series_${n}`, `Series ${n}`),
)
const SERIES = [WALKER, MERCURY, ...OTHERS]

function renderPicker(props = {}, values = SERIES) {
  const onChange = vi.fn()
  renderWithProviders(
    <ReferenceSelect table="series" value="" onChange={onChange} {...props} />,
    { reference: emptyReference({ tables: { series: values } }) },
  )
  return onChange
}

function offered() {
  return within(screen.getByRole('combobox', { name: 'series' }))
    .getAllByRole('option')
    .map((o) => o.textContent)
}

describe('entryMatch', () => {
  it('matches a label or code without naming an alias', () => {
    expect(entryMatch(WALKER, 'walking')).toEqual({ alias: null })
    expect(entryMatch(WALKER, 'LIBERTY_HALF')).toEqual({ alias: null })
    expect(entryMatch(WALKER, '')).toEqual({ alias: null })
  })

  it('names the alias that matched', () => {
    expect(entryMatch(WALKER, 'walk')).toEqual({ alias: null })
    expect(entryMatch(WALKER, 'walker')).toEqual({ alias: 'Walker' })
    expect(entryMatch(MERCURY, 'merc')).toEqual({ alias: 'Mercury' })
  })

  it('does not match what it does not name', () => {
    expect(entryMatch(WALKER, 'mercury')).toBeNull()
    expect(entryMatch({ code: 'x', label: 'X' }, 'y')).toBeNull()
  })

  it('finds entries in their own order', () => {
    expect(findEntries(SERIES, 'liberty').map(({ entry }) => entry.code)).toEqual([
      'walking_liberty_half',
      'winged_liberty_head_dime',
    ])
  })
})

describe('ReferenceSelect find box', () => {
  it('offers a value by its alias, and says which alias', async () => {
    const user = userEvent.setup()
    renderPicker()
    await user.type(screen.getByRole('searchbox', { name: 'Find series' }), 'Walker')

    expect(offered()).toEqual([
      '--',
      'Walking Liberty Half Dollar (Walker)',
      '+ Add a new value...',
    ])
  })

  it('picks the first match on Enter', async () => {
    const user = userEvent.setup()
    const onChange = renderPicker()
    await user.type(
      screen.getByRole('searchbox', { name: 'Find series' }),
      'merc{Enter}',
    )

    expect(onChange).toHaveBeenCalledWith({
      target: { value: 'winged_liberty_head_dime' },
    })
  })

  it('says when nothing matches', async () => {
    const user = userEvent.setup()
    const onChange = renderPicker()
    await user.type(
      screen.getByRole('searchbox', { name: 'Find series' }),
      'zzz{Enter}',
    )

    expect(offered()).toContain('nothing matches zzz')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('keeps the chosen value visible while finding another', async () => {
    const user = userEvent.setup()
    renderPicker({ value: 'winged_liberty_head_dime' })
    await user.type(screen.getByRole('searchbox', { name: 'Find series' }), 'walker')

    expect(offered()).toEqual([
      '--',
      'Winged Liberty Head Dime',
      'Walking Liberty Half Dollar (Walker)',
      '+ Add a new value...',
    ])
  })

  it('leaves a short vocabulary without one', () => {
    renderPicker({}, [WALKER, MERCURY])
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(offered()).toContain('Walking Liberty Half Dollar')
  })
})
