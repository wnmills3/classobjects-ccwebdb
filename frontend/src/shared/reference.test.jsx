import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  api: { addReferenceValue: vi.fn() },
}))

import { api } from './api'
import { FIND_FROM, ReferenceSelect } from './reference'
import { useReference } from './reference-context'
import { entryMatch, findEntries } from './reference-match'
import { codeFromLabel } from './reference-codes'
import { emptyReference, renderWithProviders } from '../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
})

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

describe('codeFromLabel', () => {
  it.each([
    ['Mismatched Serial', 'mismatched_serial'],
    ['  Gutter fold  ', 'gutter_fold'],
    ["Printer's Mark", 'printers_mark'],
    ['Off-Center', 'off_center'],
    ['Ink Smear 2', 'ink_smear_2'],
  ])('%s becomes %s', (label, code) => {
    expect(codeFromLabel(label)).toBe(code)
  })
})

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

  it('shows a retired value an item still holds, and offers no other', async () => {
    const retired = { ...value('old_series', 'Old Series'), is_active: false }
    const gone = { ...value('gone_series', 'Gone Series'), is_active: false }
    renderPicker({ value: 'old_series' }, [...SERIES, retired, gone])
    const options = offered()
    expect(options).toContain('Old Series (retired)')
    expect(options.join()).not.toContain('Gone Series')
  })

  it('counts only active values when deciding on a find box', () => {
    const retired = Array.from({ length: FIND_FROM }, (_, n) => ({
      ...value(`old_${n}`, `Old ${n}`),
      is_active: false,
    }))
    renderPicker({}, [WALKER, MERCURY, ...retired])
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(offered()).toEqual([
      '--',
      'Walking Liberty Half Dollar',
      'Winged Liberty Head Dime',
      '+ Add a new value...',
    ])
  })
})

describe('ReferenceSelect adding a value by its label alone', () => {
  const ATTRIBUTES = [value('star_note', 'Star Note'), value('no_motto', 'No Motto')]

  function renderAttributePicker(props = {}, values = ATTRIBUTES) {
    const onChange = vi.fn()
    renderWithProviders(
      <ReferenceSelect
        table="item_attribute"
        value=""
        onChange={onChange}
        allowAdd
        labelOnly
        addFields={{ applies_to: 'currency' }}
        {...props}
      />,
      { reference: emptyReference({ tables: { item_attribute: values } }) },
    )
    return onChange
  }

  async function openAddForm(user, name = 'item_attribute') {
    await user.selectOptions(screen.getByRole('combobox', { name }), '__add__')
  }

  it('posts the derived code, extra columns nested under extra, and selects it', async () => {
    const user = userEvent.setup()
    api.addReferenceValue.mockResolvedValue({})
    const onChange = renderAttributePicker()
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Mismatched Serial')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.addReferenceValue).toHaveBeenCalledWith('item_attribute', {
      code: 'mismatched_serial',
      label: 'Mismatched Serial',
      extra: { applies_to: 'currency' },
    })
    expect(onChange).toHaveBeenCalledWith({ target: { value: 'mismatched_serial' } })
  })

  it('shows the derived code before saving', async () => {
    const user = userEvent.setup()
    renderAttributePicker()
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Off-Center')

    expect(screen.getByText('off_center')).toBeInTheDocument()
  })

  it('selects an existing value instead of posting when the derived code already exists', async () => {
    const user = userEvent.setup()
    const onChange = renderAttributePicker()
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Star Note')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.addReferenceValue).not.toHaveBeenCalled()
    expect(onChange).toHaveBeenCalledWith({ target: { value: 'star_note' } })
  })

  it('asks for code and label as before when labelOnly is not set', async () => {
    const user = userEvent.setup()
    renderPicker()
    await openAddForm(user, 'series')

    expect(screen.getByPlaceholderText('code')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('label')).toBeInTheDocument()
  })

  it('disables Add for a label that derives to an empty code', async () => {
    const user = userEvent.setup()
    renderAttributePicker()
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), '!!!')

    expect(document.querySelector('.derived-code')).toHaveTextContent('')
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(api.addReferenceValue).not.toHaveBeenCalled()
  })

  it('renders no group picker, and requires none, when groupField is not given', async () => {
    const user = userEvent.setup()
    api.addReferenceValue.mockResolvedValue({})
    const onChange = renderAttributePicker()
    await openAddForm(user)

    expect(screen.queryByRole('combobox', { name: 'group' })).not.toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('label'), 'Mismatched Serial')
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(onChange).toHaveBeenCalledWith({ target: { value: 'mismatched_serial' } })
  })

  describe('with a required group', () => {
    const GROUPS = [
      { code: 'serial', label: 'Serial' },
      { code: 'variety', label: 'Variety' },
    ]

    it('keeps Add disabled until a group is chosen, then sends it under extra', async () => {
      const user = userEvent.setup()
      api.addReferenceValue.mockResolvedValue({})
      const onChange = renderAttributePicker({
        groupField: 'attribute_group',
        groupOptions: GROUPS,
      })
      await openAddForm(user)
      await user.type(screen.getByPlaceholderText('label'), 'Mismatched Serial')

      expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()

      await user.selectOptions(
        screen.getByRole('combobox', { name: 'group' }),
        'serial',
      )
      await user.click(screen.getByRole('button', { name: 'Add' }))

      expect(api.addReferenceValue).toHaveBeenCalledWith('item_attribute', {
        code: 'mismatched_serial',
        label: 'Mismatched Serial',
        extra: { applies_to: 'currency', attribute_group: 'serial' },
      })
      expect(onChange).toHaveBeenCalledWith({ target: { value: 'mismatched_serial' } })
    })

    it('never posts without a group chosen, even if Add is clicked', async () => {
      const user = userEvent.setup()
      renderAttributePicker({ groupField: 'attribute_group', groupOptions: GROUPS })
      await openAddForm(user)
      await user.type(screen.getByPlaceholderText('label'), 'Mismatched Serial')
      await user.click(screen.getByRole('button', { name: 'Add' }))

      expect(api.addReferenceValue).not.toHaveBeenCalled()
    })
  })
})

describe('useReference', () => {
  function Show({ includeRetired }) {
    const values = useReference('series', { includeRetired }) ?? []
    return <p>{values.map((v) => v.code).join(',')}</p>
  }

  it('leaves retired values out unless asked', () => {
    const tables = {
      series: [value('a', 'A'), { ...value('b', 'B'), is_active: false }],
    }
    const { unmount } = renderWithProviders(<Show />, {
      reference: emptyReference({ tables }),
    })
    expect(screen.getByText('a')).toBeInTheDocument()
    unmount()
    renderWithProviders(<Show includeRetired />, {
      reference: emptyReference({ tables }),
    })
    expect(screen.getByText('a,b')).toBeInTheDocument()
  })
})
