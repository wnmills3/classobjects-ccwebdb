import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listReferenceTables: vi.fn(),
    getReferenceForEditing: vi.fn(),
    addReferenceAlias: vi.fn(),
    removeReferenceAlias: vi.fn(),
  },
}))

import { api } from '../api'
import Vocabularies from './Vocabularies'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

function value(code, label, aliases = [], extra = {}) {
  return {
    code,
    label,
    sort_order: 0,
    source: 'seeded',
    is_active: true,
    extra: {},
    aliases,
    retired_aliases: [],
    ...extra,
  }
}

const US_NOTE = value('us_note', 'United States Note', ['Legal Tender Note'], {
  retired_aliases: ['Legal Tender'],
})
const NATIONAL = value('national_bank_note', 'National Bank Note', [
  'National Currency',
])
const FRBN = value('frbn', 'Federal Reserve Bank Note', ['National Currency'])
const OLD = value('old_type', 'Old Type', [], { is_active: false })

let reference

beforeEach(() => {
  vi.clearAllMocks()
  reference = emptyReference()
  api.listReferenceTables.mockResolvedValue(['series', 'note_type'])
  api.getReferenceForEditing.mockImplementation(async (table) => ({
    table,
    values:
      table === 'note_type'
        ? [US_NOTE, NATIONAL, FRBN, OLD]
        : [value('walking_liberty_half', 'Walking Liberty Half Dollar', ['Walker'])],
  }))
})

async function openNoteTypes(user) {
  renderWithProviders(<Vocabularies />, { auth: adminAuth(), reference })
  await screen.findByText('Walking Liberty Half Dollar')
  await user.selectOptions(screen.getByLabelText('Vocabulary'), 'note_type')
  await screen.findByText('United States Note')
}

// By the label cell: a message in the row may repeat the label.
function row(label) {
  return screen
    .getAllByRole('row')
    .find((r) => r.cells[0]?.textContent.startsWith(label))
}

describe('Vocabularies', () => {
  it('opens on the series and lists each value with its aliases', async () => {
    renderWithProviders(<Vocabularies />, { auth: adminAuth(), reference })
    expect(await screen.findByText('Walker')).toBeInTheDocument()
    expect(api.getReferenceForEditing).toHaveBeenCalledWith('series')
  })

  it('marks an alias two values share, and a retired value', async () => {
    const user = userEvent.setup()
    await openNoteTypes(user)

    expect(within(row('National Bank Note')).getByText('(shared)')).toBeInTheDocument()
    expect(within(row('United States Note')).queryByText('(shared)')).toBeNull()
    expect(row('Old Type')).toHaveTextContent('Old Type (retired)')
  })

  it('adds an alias and tells the pickers their copy is stale', async () => {
    const user = userEvent.setup()
    api.addReferenceAlias.mockResolvedValue({
      ...US_NOTE,
      aliases: ['Greenback', 'Legal Tender Note'],
    })
    await openNoteTypes(user)

    await user.type(
      screen.getByLabelText('New alias for United States Note'),
      'Greenback{Enter}',
    )

    expect(api.addReferenceAlias).toHaveBeenCalledWith(
      'note_type',
      'us_note',
      'Greenback',
    )
    expect(
      await within(row('United States Note')).findByText('Greenback'),
    ).toBeVisible()
    expect(screen.getByLabelText('New alias for United States Note')).toHaveValue('')
    expect(reference.invalidate).toHaveBeenCalledWith('note_type')
  })

  it('shows a refusal beside the value and keeps what was typed', async () => {
    const user = userEvent.setup()
    api.addReferenceAlias.mockRejectedValue(
      new Error("'Legal Tender Note' is already an alias of United States Note."),
    )
    await openNoteTypes(user)

    const input = screen.getByLabelText('New alias for United States Note')
    await user.type(input, 'legal tender note')
    await user.click(
      within(row('United States Note')).getByRole('button', { name: 'Add' }),
    )

    expect(
      await within(row('United States Note')).findByText(/already an alias/),
    ).toBeVisible()
    expect(input).toHaveValue('legal tender note')
  })

  it('removes an alias and restores a retired one', async () => {
    const user = userEvent.setup()
    api.removeReferenceAlias.mockResolvedValue({
      ...US_NOTE,
      aliases: [],
      retired_aliases: ['Legal Tender', 'Legal Tender Note'],
    })
    api.addReferenceAlias.mockResolvedValue({
      ...US_NOTE,
      aliases: ['Legal Tender', 'Legal Tender Note'],
      retired_aliases: [],
    })
    await openNoteTypes(user)

    await user.click(
      screen.getByRole('button', {
        name: 'Remove Legal Tender Note from United States Note',
      }),
    )
    expect(api.removeReferenceAlias).toHaveBeenCalledWith(
      'note_type',
      'us_note',
      'Legal Tender Note',
    )
    expect(
      await screen.findByRole('button', {
        name: 'Restore Legal Tender Note to United States Note',
      }),
    ).toBeVisible()

    await user.click(
      screen.getByRole('button', {
        name: 'Restore Legal Tender to United States Note',
      }),
    )
    expect(api.addReferenceAlias).toHaveBeenCalledWith(
      'note_type',
      'us_note',
      'Legal Tender',
    )
    expect(
      await screen.findByRole('button', {
        name: 'Remove Legal Tender from United States Note',
      }),
    ).toBeVisible()
  })

  it('finds a value by an alias', async () => {
    const user = userEvent.setup()
    await openNoteTypes(user)

    await user.type(screen.getByLabelText('Find'), 'legal tender')

    expect(screen.getByText('United States Note')).toBeVisible()
    expect(screen.queryByText('National Bank Note')).toBeNull()
  })
})
