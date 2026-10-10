import userEvent from '@testing-library/user-event'
import { render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode, useContext, useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  api: { addReferenceValue: vi.fn(), getReference: vi.fn() },
}))

import { api } from './api'
import { FIND_FROM, ReferenceProvider, ReferenceSelect } from './reference'
import { ReferenceContext, useReference } from './reference-context'
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
    // Whatever is not a letter or a digit, however much of it, is one
    // underscore inside the code and nothing at either end of it.
    ['__Ink -- Smear__', 'ink_smear'],
    ['(Star) Note!!', 'star_note'],
    ['---', ''],
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
      '+ Add a new value...',
      'Walking Liberty Half Dollar (Walker)',
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
      '+ Add a new value...',
      'Winged Liberty Head Dime',
      'Walking Liberty Half Dollar (Walker)',
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
      '+ Add a new value...',
      'Walking Liberty Half Dollar',
      'Winged Liberty Head Dime',
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

  it('selects an existing value instead of posting, and says so', async () => {
    // Selected silently, a value typed again looks like an add that did
    // nothing.
    const user = userEvent.setup()
    const onChange = renderAttributePicker()
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Star Note')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.addReferenceValue).not.toHaveBeenCalled()
    expect(onChange).toHaveBeenCalledWith({ target: { value: 'star_note' } })
    expect(screen.getByRole('status')).toHaveTextContent(
      'Star Note already exists, so it was chosen rather than added again.',
    )
  })

  it('refuses, rather than invisibly choosing, an existing value that is retired', async () => {
    const user = userEvent.setup()
    const onChange = renderAttributePicker({}, [
      ...ATTRIBUTES,
      { ...value('flipper', 'Flipper'), is_active: false },
    ])
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Flipper')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.addReferenceValue).not.toHaveBeenCalled()
    expect(onChange).not.toHaveBeenCalled()
    expect(
      screen.getByText(/Flipper already exists but is retired/),
    ).toBeInTheDocument()
  })

  it('refuses an existing value this picker does not offer, naming why', async () => {
    // AttributesField filters out what the item already carries and what
    // belongs to the other kind of item; choosing it anyway would do nothing
    // visible.
    const user = userEvent.setup()
    const onChange = renderAttributePicker({
      filter: (entry) => entry.code !== 'star_note',
    })
    await openAddForm(user)
    await user.type(screen.getByPlaceholderText('label'), 'Star Note')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.addReferenceValue).not.toHaveBeenCalled()
    expect(onChange).not.toHaveBeenCalled()
    expect(
      screen.getByText(/Star Note already exists but is not offered here/),
    ).toBeInTheDocument()
  })

  it('shows a value added in the app by its label alone, with no marker', () => {
    // Where a value came from is not the picker's business: a marker after
    // the label would read as part of the name.
    renderAttributePicker({}, [
      ...ATTRIBUTES,
      { ...value('flipper', 'Flipper'), source: 'manual' },
    ])
    expect(screen.getByRole('option', { name: 'Flipper' })).toBeInTheDocument()
  })

  it('asks for code and label when labelOnly is not set', async () => {
    const user = userEvent.setup()
    renderPicker()
    await openAddForm(user, 'series')

    expect(screen.getByPlaceholderText('code')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('label')).toBeInTheDocument()
  })

  it('adds by the label alone when the code box is left empty', async () => {
    // A label with the code box left empty is enough: the code is derived
    // from the label, so Add must not stay disabled.
    const user = userEvent.setup()
    api.addReferenceValue.mockResolvedValue({})
    renderPicker()
    await openAddForm(user, 'series')
    await user.type(screen.getByPlaceholderText('label'), 'Mixed Set')

    expect(screen.getByPlaceholderText('code: mixed_set')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(api.addReferenceValue).toHaveBeenCalledWith('series', {
      code: 'mixed_set',
      label: 'Mixed Set',
      extra: {},
    })
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

describe('ReferenceProvider', () => {
  function Codes({ table = 'series' }) {
    const values = useReference(table)
    return (
      <p>
        {values === undefined
          ? 'loading'
          : values.map((v) => v.code).join(',') || 'none'}
      </p>
    )
  }

  function Invalidate() {
    const { invalidate } = useContext(ReferenceContext)
    return (
      <button type="button" onClick={() => invalidate('series')}>
        invalidate
      </button>
    )
  }

  it('fetches a vocabulary once for every picker that asks for it', async () => {
    // A form mounts five pickers over the same table in one commit. Each one
    // asks before any answer has arrived; only the first may fetch.
    api.getReference.mockResolvedValue({ values: [value('a', 'A')] })
    render(
      <ReferenceProvider>
        <Codes />
        <Codes />
      </ReferenceProvider>,
    )
    expect(await screen.findAllByText('a')).toHaveLength(2)
    expect(api.getReference).toHaveBeenCalledTimes(1)
  })

  it('fetches once under StrictMode too', async () => {
    // StrictMode runs every effect twice on mount, which is how the console
    // runs in development.
    api.getReference.mockResolvedValue({ values: [value('a', 'A')] })
    render(
      <StrictMode>
        <ReferenceProvider>
          <Codes />
        </ReferenceProvider>
      </StrictMode>,
    )
    expect(await screen.findByText('a')).toBeInTheDocument()
    expect(api.getReference).toHaveBeenCalledTimes(1)
  })

  it('fetches a vocabulary again once it has been invalidated', async () => {
    const user = userEvent.setup()
    api.getReference
      .mockResolvedValueOnce({ values: [value('a', 'A')] })
      .mockResolvedValueOnce({ values: [value('a', 'A'), value('b', 'B')] })
    render(
      <ReferenceProvider>
        <Codes />
        <Invalidate />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('a')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'invalidate' }))

    expect(await screen.findByText('a,b')).toBeInTheDocument()
    expect(api.getReference).toHaveBeenCalledTimes(2)
  })

  it('fetches an old copy again when a picker next appears, keeping it meanwhile', async () => {
    // A value merged away elsewhere must not stay on offer for the life of
    // the page: picking it would be refused by the server.
    const user = userEvent.setup()
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    let answer
    api.getReference
      .mockResolvedValueOnce({ values: [value('a', 'A'), value('gone', 'Gone')] })
      .mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))

    function Later() {
      const [shown, setShown] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setShown(true)}>
            open
          </button>
          {shown && <Codes />}
        </>
      )
    }
    render(
      <ReferenceProvider>
        <Codes />
        <Later />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('a,gone')).toBeInTheDocument()

    // Two minutes on, a form opens over the same vocabulary.
    now.mockReturnValue(1_000_000 + 120_000)
    await user.click(screen.getByRole('button', { name: 'open' }))
    await waitFor(() => expect(api.getReference).toHaveBeenCalledTimes(2))
    // The copy held is still shown while the answer is out.
    expect(screen.getAllByText('a,gone')).toHaveLength(2)

    answer({ values: [value('a', 'A')] })
    await waitFor(() => expect(screen.getAllByText('a')).toHaveLength(2))
    now.mockRestore()
  })

  it('does not fetch a fresh copy again', async () => {
    const user = userEvent.setup()
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    api.getReference.mockResolvedValue({ values: [value('a', 'A')] })

    function Later() {
      const [shown, setShown] = useState(false)
      return shown ? (
        <Codes />
      ) : (
        <button type="button" onClick={() => setShown(true)}>
          open
        </button>
      )
    }
    render(
      <ReferenceProvider>
        <Codes />
        <Later />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('a')).toBeInTheDocument()

    now.mockReturnValue(1_000_000 + 30_000)
    await user.click(screen.getByRole('button', { name: 'open' }))
    expect(await screen.findAllByText('a')).toHaveLength(2)
    expect(api.getReference).toHaveBeenCalledTimes(1)
    now.mockRestore()
  })

  it('fetches an old copy again when the window gets focus back', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    api.getReference
      .mockResolvedValueOnce({ values: [value('a', 'A'), value('gone', 'Gone')] })
      .mockResolvedValueOnce({ values: [value('a', 'A')] })
    render(
      <ReferenceProvider>
        <Codes />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('a,gone')).toBeInTheDocument()

    // Straight back: nothing to do.
    window.dispatchEvent(new Event('focus'))
    expect(api.getReference).toHaveBeenCalledTimes(1)

    now.mockReturnValue(1_000_000 + 120_000)
    window.dispatchEvent(new Event('focus'))
    expect(await screen.findByText('a')).toBeInTheDocument()
    expect(api.getReference).toHaveBeenCalledTimes(2)
    now.mockRestore()
  })

  it('keeps the copy it has when a refresh cannot be fetched', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    api.getReference
      .mockResolvedValueOnce({ values: [value('a', 'A')] })
      .mockRejectedValueOnce(new Error('offline'))
    render(
      <ReferenceProvider>
        <Codes />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('a')).toBeInTheDocument()

    now.mockReturnValue(1_000_000 + 120_000)
    window.dispatchEvent(new Event('focus'))
    await waitFor(() => expect(api.getReference).toHaveBeenCalledTimes(2))
    expect(screen.getByText('a')).toBeInTheDocument()
    now.mockRestore()
  })

  it('still delivers a vocabulary invalidated while its first load is in flight', async () => {
    // The first answer is dropped as stale; the vocabulary must not then be
    // left missing with nothing asking for it again.
    const user = userEvent.setup()
    let answerFirst
    api.getReference
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            answerFirst = resolve
          }),
      )
      .mockResolvedValueOnce({ values: [value('a', 'A'), value('b', 'B')] })
    render(
      <ReferenceProvider>
        <Codes />
        <Invalidate />
      </ReferenceProvider>,
    )
    await waitFor(() => expect(api.getReference).toHaveBeenCalledTimes(1))
    expect(screen.getByText('loading')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'invalidate' }))
    answerFirst({ values: [value('a', 'A')] })

    expect(await screen.findByText('a,b')).toBeInTheDocument()
    expect(api.getReference).toHaveBeenCalledTimes(2)
  })

  it('settles on an empty list, once, when the vocabulary cannot be fetched', async () => {
    // Empty, so the picker falls back to a text box; once, so a table the
    // server refuses is not asked for again on every render.
    api.getReference.mockRejectedValue(new Error('offline'))
    render(
      <ReferenceProvider>
        <Codes />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('none')).toBeInTheDocument()
    await waitFor(() => expect(api.getReference).toHaveBeenCalledTimes(1))
  })
})

describe('ReferenceSelect over a vocabulary that could not be fetched', () => {
  function Picker() {
    const [chosen, setChosen] = useState('')
    return (
      <ReferenceSelect
        table="series"
        value={chosen}
        onChange={(e) => setChosen(e.target.value)}
      />
    )
  }

  it('says it could not load, and still takes typed text', async () => {
    // A text box with no word said reads as a field that was always free
    // text; what is typed into it is then refused on save as an unknown code.
    const user = userEvent.setup()
    api.getReference.mockRejectedValue(new Error('offline'))
    render(
      <ReferenceProvider>
        <Picker />
      </ReferenceProvider>,
    )

    expect(await screen.findByText('Could not load series.')).toBeInTheDocument()
    const box = screen.getByRole('textbox', { name: 'series' })
    await user.type(box, 'morgan')
    expect(box).toHaveValue('morgan')
  })

  it('says nothing of the kind while a vocabulary is loading or simply empty', async () => {
    api.getReference.mockResolvedValue({ values: [] })
    render(
      <ReferenceProvider>
        <Picker />
      </ReferenceProvider>,
    )

    await waitFor(() => expect(api.getReference).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('textbox', { name: 'series' })).toBeInTheDocument()
    expect(screen.queryByText(/could not load/i)).toBeNull()
  })

  it('stops saying so once a later fetch succeeds', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    api.getReference
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ values: [value('a', 'A')] })
    render(
      <ReferenceProvider>
        <Picker />
      </ReferenceProvider>,
    )
    expect(await screen.findByText('Could not load series.')).toBeInTheDocument()

    now.mockReturnValue(1_000_000 + 120_000)
    window.dispatchEvent(new Event('focus'))

    expect(await screen.findByRole('combobox', { name: 'series' })).toBeInTheDocument()
    expect(screen.queryByText(/could not load/i)).toBeNull()
    now.mockRestore()
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

describe('ReferenceSelect: disabled', () => {
  it('disables the select and its find box', () => {
    renderPicker({ disabled: true })
    expect(screen.getByRole('combobox', { name: 'series' })).toBeDisabled()
    expect(screen.getByRole('searchbox', { name: 'Find series' })).toBeDisabled()
  })

  it('disables the text box shown before the vocabulary loads', () => {
    renderPicker({ disabled: true }, [])
    expect(screen.getByRole('textbox', { name: 'series' })).toBeDisabled()
  })

  it('is enabled unless asked', () => {
    renderPicker()
    expect(screen.getByRole('combobox', { name: 'series' })).toBeEnabled()
  })
})
