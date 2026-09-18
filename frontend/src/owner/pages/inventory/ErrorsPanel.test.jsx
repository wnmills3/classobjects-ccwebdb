import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { getItemErrors: vi.fn(), setItemErrors: vi.fn() },
}))

// ReferenceSelect's "add a value" posts through shared/api.js, not owner/api.js
// -- see the module boundary note in reference.jsx -- so it needs its own mock.
vi.mock('../../../shared/api', () => ({
  api: { addReferenceValue: vi.fn() },
}))

import { api } from '../../api'
import { api as sharedApi } from '../../../shared/api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import ErrorsPanel from './ErrorsPanel'

const errorType = (code, label, applies_to) => ({
  code,
  label,
  source: 'seeded',
  aliases: [],
  extra: { applies_to },
})

const vocabularies = emptyReference({
  tables: {
    error_type: [
      errorType('miscut', 'Miscut', 'any'),
      errorType('off_center_coin', 'Off Center', 'coin'),
      errorType('inverted_overprint', 'Inverted Overprint', 'currency'),
    ],
  },
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ErrorsPanel, self-loading (itemId set)', () => {
  it('loads and renders existing errors with their notes', async () => {
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [
        {
          error_type: 'miscut',
          details: "miscut at 3 o'clock",
          source: 'manual',
          noted_by_id: 1,
          noted_at: '2026-09-01T00:00:00Z',
        },
      ],
    })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    expect(await screen.findByText('Miscut')).toBeVisible()
    expect(screen.getByDisplayValue("miscut at 3 o'clock")).toBeVisible()
    expect(api.getItemErrors).toHaveBeenCalledWith(12)
  })

  it('adds a type and a note, saving the exact array', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.type(screen.getByPlaceholderText('details'), "miscut at 3 o'clock")
    await user.click(screen.getByRole('button', { name: 'Add error' }))

    expect(api.setItemErrors).toHaveBeenCalledWith(12, [
      { error_type: 'miscut', details: "miscut at 3 o'clock" },
    ])
  })

  it('does not offer a type already recorded', async () => {
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [{ error_type: 'miscut', details: null, source: 'manual', noted_at: '' }],
    })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByText('Miscut')
    const options = within(
      screen.getByRole('combobox', { name: 'error_type' }),
    ).getAllByRole('option')
    expect(options.map((o) => o.textContent)).not.toContain('Miscut')
  })

  it('offers only the item kind', async () => {
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="coin" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })
    const options = within(
      screen.getByRole('combobox', { name: 'error_type' }),
    ).getAllByRole('option')
    const labels = options.map((o) => o.textContent)
    expect(labels).toContain('Off Center')
    expect(labels).not.toContain('Inverted Overprint')
  })

  it('removing a row and saving sends the remaining set', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [
        { error_type: 'miscut', details: 'note a', source: 'manual', noted_at: '' },
        {
          error_type: 'off_center_coin',
          details: 'note b',
          source: 'manual',
          noted_at: '',
        },
      ],
    })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="coin" />, {
      reference: vocabularies,
    })
    await screen.findByText('Miscut')

    await user.click(screen.getByRole('button', { name: 'Remove Miscut' }))

    expect(api.setItemErrors).toHaveBeenCalledWith(12, [
      { error_type: 'off_center_coin', details: 'note b' },
    ])
    // Scoped to the recorded-error list: "Miscut" freed up by the removal is
    // correctly still offered again by the picker below it.
    expect(within(screen.getByRole('list')).queryByText('Miscut')).toBeNull()
  })

  it('saves an edited note on an existing row when its box loses focus', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [
        { error_type: 'miscut', details: 'note a', source: 'manual', noted_at: '' },
      ],
    })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    const input = await screen.findByLabelText('Miscut details')

    await user.clear(input)
    await user.type(input, 'corrected note')
    await user.tab()

    expect(api.setItemErrors).toHaveBeenCalledWith(12, [
      { error_type: 'miscut', details: 'corrected note' },
    ])
  })

  it('shows the message and keeps the rows when a save fails', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    api.setItemErrors.mockRejectedValue(new Error('server is unhappy'))
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.type(screen.getByPlaceholderText('details'), "miscut at 3 o'clock")
    await user.click(screen.getByRole('button', { name: 'Add error' }))

    expect(await screen.findByText('server is unhappy')).toBeVisible()
    expect(screen.getByText('Miscut')).toBeVisible()
    expect(screen.getByDisplayValue("miscut at 3 o'clock")).toBeVisible()
  })
})

describe('ErrorsPanel, controlled (itemId null)', () => {
  it('never calls the API and reports changes through onChange', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithProviders(
      <ErrorsPanel itemId={null} kind="currency" value={[]} onChange={onChange} />,
      { reference: vocabularies },
    )

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.type(screen.getByPlaceholderText('details'), "miscut at 3 o'clock")
    await user.click(screen.getByRole('button', { name: 'Add error' }))

    expect(onChange).toHaveBeenCalledWith([
      { error_type: 'miscut', details: "miscut at 3 o'clock" },
    ])
    expect(api.getItemErrors).not.toHaveBeenCalled()
    expect(api.setItemErrors).not.toHaveBeenCalled()
  })

  it('renders the rows it is given and removes one through onChange', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithProviders(
      <ErrorsPanel
        itemId={null}
        kind="currency"
        value={[{ error_type: 'miscut', details: 'a note' }]}
        onChange={onChange}
      />,
      { reference: vocabularies },
    )

    expect(screen.getByText('Miscut')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Remove Miscut' }))
    expect(onChange).toHaveBeenCalledWith([])
  })
})

// ReferenceSelect's own add flow, exercised here since ErrorsPanel is the
// caller that supplies addFields/labelOnly/allowAdd -- see the module
// boundary note in ItemEditForm.test.jsx's Attributes block.
describe('ErrorsPanel, adding an error type from the picker', () => {
  it("adds a value by its label alone, marked for the item's side", async () => {
    const user = userEvent.setup()
    sharedApi.addReferenceValue.mockResolvedValue({})
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="coin" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      '__add__',
    )
    await user.type(screen.getByPlaceholderText('label'), 'Double Strike')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    // extra columns nested under `extra`; error_type has no attribute_group.
    expect(sharedApi.addReferenceValue).toHaveBeenCalledWith('error_type', {
      code: 'double_strike',
      label: 'Double Strike',
      extra: { applies_to: 'coin' },
    })
  })
})
