import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listReferenceTables: vi.fn(),
    getReferenceForEditing: vi.fn(),
    addReferenceAlias: vi.fn(),
    renameReferenceValue: vi.fn(),
    mergeReferenceValue: vi.fn(),
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
const FRN = value('frn_code', 'Federal Reserve Note', [], { retirable: false })

let reference

beforeEach(() => {
  vi.clearAllMocks()
  reference = emptyReference()
  api.listReferenceTables.mockResolvedValue(['series', 'note_type'])
  api.getReferenceForEditing.mockImplementation(async (table) => ({
    table,
    values:
      table === 'note_type'
        ? [US_NOTE, NATIONAL, FRBN, OLD, FRN]
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

  it('renames a value, sending its code and the new label', async () => {
    const user = userEvent.setup()
    api.renameReferenceValue.mockResolvedValue({
      ...US_NOTE,
      label: 'U.S. Note',
      source: 'manual',
    })
    await openNoteTypes(user)

    await user.click(screen.getByRole('button', { name: 'Rename United States Note' }))
    const input = screen.getByLabelText('New name for United States Note')
    await user.clear(input)
    await user.type(input, 'U.S. Note{Enter}')

    expect(api.renameReferenceValue).toHaveBeenCalledWith('note_type', 'us_note', {
      label: 'U.S. Note',
    })
    expect(await screen.findByText('U.S. Note')).toBeVisible()
    expect(screen.queryByLabelText('New name for United States Note')).toBeNull()
    expect(reference.invalidate).toHaveBeenCalledWith('note_type')
  })

  it('cancels a rename without saving', async () => {
    const user = userEvent.setup()
    await openNoteTypes(user)
    await user.click(screen.getByRole('button', { name: 'Rename United States Note' }))
    await user.type(screen.getByLabelText('New name for United States Note'), 'x')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.renameReferenceValue).not.toHaveBeenCalled()
    expect(row('United States Note')).toBeTruthy()
  })

  it('retires a value and restores one', async () => {
    const user = userEvent.setup()
    api.renameReferenceValue.mockImplementation(async (table, code, changes) => ({
      ...(code === 'us_note' ? US_NOTE : OLD),
      ...changes,
    }))
    await openNoteTypes(user)

    await user.click(screen.getByRole('button', { name: 'Retire United States Note' }))
    expect(api.renameReferenceValue).toHaveBeenCalledWith('note_type', 'us_note', {
      label: 'United States Note',
      is_active: false,
    })
    expect(await screen.findByText('United States Note (retired)')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Restore Old Type' }))
    expect(api.renameReferenceValue).toHaveBeenCalledWith('note_type', 'old_type', {
      label: 'Old Type',
      is_active: true,
    })
    expect(await screen.findByRole('button', { name: 'Retire Old Type' })).toBeVisible()
  })

  it('does not offer to retire a value the application looks up', async () => {
    const user = userEvent.setup()
    await openNoteTypes(user)
    const retire = screen.getByRole('button', { name: 'Retire Federal Reserve Note' })
    expect(retire).toBeDisabled()
    expect(retire).toHaveAttribute(
      'title',
      'The application looks this value up by its code',
    )
    expect(
      screen.getByRole('button', { name: 'Rename Federal Reserve Note' }),
    ).toBeEnabled()
  })

  it('shows a refusal beside the value', async () => {
    const user = userEvent.setup()
    api.renameReferenceValue.mockRejectedValue(new Error('A label needs some text.'))
    await openNoteTypes(user)
    await user.click(screen.getByRole('button', { name: 'Retire United States Note' }))
    expect(
      await within(row('United States Note')).findByText('A label needs some text.'),
    ).toBeVisible()
  })

  it('merges a value after showing what would move', async () => {
    const user = userEvent.setup()
    api.mergeReferenceValue.mockImplementation(async (table, code, into, dryRun) => ({
      table,
      code,
      into,
      dry_run: dryRun,
      moved: { 'currency_detail.note_type_id': 3 },
      items: 3,
      dropped: 0,
      aliases: ['National Bank Note', 'national_bank_note'],
    }))
    await openNoteTypes(user)

    await user.click(
      screen.getByRole('button', {
        name: 'Merge National Bank Note into another value',
      }),
    )
    const choice = screen.getByLabelText('Merge National Bank Note into')
    // Only active values other than itself are offered.
    expect(within(choice).queryByText('Old Type')).toBeNull()
    expect(within(choice).queryByText('National Bank Note')).toBeNull()
    const merge = screen.getByRole('button', { name: 'Merge' })
    expect(merge).toBeDisabled()

    await user.selectOptions(choice, 'us_note')
    expect(api.mergeReferenceValue).toHaveBeenCalledWith(
      'note_type',
      'national_bank_note',
      'us_note',
      true,
    )
    expect(
      await screen.findByText(
        'Moves 3 items to United States Note, then removes National Bank Note. ' +
          'National Bank Note, national_bank_note will find United States Note. ' +
          'This cannot be undone.',
      ),
    ).toBeVisible()

    await user.click(merge)
    expect(api.mergeReferenceValue).toHaveBeenLastCalledWith(
      'note_type',
      'national_bank_note',
      'us_note',
      false,
    )
    expect(
      await screen.findByText('Merged national_bank_note into us_note: 3 items moved.'),
    ).toBeVisible()
    // The list is read again, and pickers elsewhere are told.
    expect(api.getReferenceForEditing).toHaveBeenCalledTimes(3)
    expect(reference.invalidate).toHaveBeenCalledWith('note_type')
  })

  it('shows a refusal and does not merge', async () => {
    const user = userEvent.setup()
    api.mergeReferenceValue.mockRejectedValue(
      new Error('National Bank Note is also used by series.note_type_id (1)'),
    )
    await openNoteTypes(user)
    await user.click(
      screen.getByRole('button', {
        name: 'Merge National Bank Note into another value',
      }),
    )
    await user.selectOptions(
      screen.getByLabelText('Merge National Bank Note into'),
      'us_note',
    )
    expect(await screen.findByText(/also used by series/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Merge' })).toBeDisabled()
    expect(api.mergeReferenceValue).toHaveBeenCalledTimes(1)
  })

  it('does not offer to merge away a value the application looks up', async () => {
    const user = userEvent.setup()
    await openNoteTypes(user)
    expect(
      screen.getByRole('button', {
        name: 'Merge Federal Reserve Note into another value',
      }),
    ).toBeDisabled()
  })
})
