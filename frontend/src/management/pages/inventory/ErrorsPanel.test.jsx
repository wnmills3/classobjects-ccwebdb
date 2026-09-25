import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { getItemErrors: vi.fn(), setItemErrors: vi.fn() },
}))

// ReferenceSelect's "add a value" posts through shared/api.js, not management/api.js
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

    expect(api.setItemErrors).toHaveBeenCalledWith(
      12,
      [{ error_type: 'miscut', details: "miscut at 3 o'clock" }],
      { acknowledgeForSale: false },
    )
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

    expect(api.setItemErrors).toHaveBeenCalledWith(
      12,
      [{ error_type: 'off_center_coin', details: 'note b' }],
      { acknowledgeForSale: false },
    )
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

    expect(api.setItemErrors).toHaveBeenCalledWith(
      12,
      [{ error_type: 'miscut', details: 'corrected note' }],
      { acknowledgeForSale: false },
    )
  })

  // Rendered in StrictMode on purpose: the console runs in it (see
  // `management/main.jsx`), React then runs every effect setup/cleanup/setup on
  // mount, and this panel once carried an `if (mounted.current)` guard that
  // its own cleanup disarmed and no setup re-armed. Both branches of `save()`
  // were dead for the rest of the panel's life, so in the owner's real
  // console a failed PUT left the row looking saved with nothing said. Only a
  // StrictMode render can see that, which is why this one asks for it.
  it('names itself, so the picker is not a stray dropdown', async () => {
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })

    // The heading, and the box beside the picker, both say what they are --
    // the panel used to offer neither, in any of its three mount points.
    expect(screen.getByText('Errors')).toBeVisible()
    expect(
      screen.getByRole('textbox', { name: 'details for the error being added' }),
    ).toBeVisible()
  })

  it('shows a retired type by its label, not its raw code', async () => {
    // A type recorded against an item can be retired afterwards. The panel
    // asks for retired values for exactly this reason (ReferenceSelect does
    // the same); without them the row falls back to showing `miscut`.
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [{ error_type: 'miscut', details: null, source: 'manual', noted_at: '' }],
    })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: emptyReference({
        tables: {
          error_type: [{ ...errorType('miscut', 'Miscut', 'any'), is_active: false }],
        },
      }),
    })

    expect(await screen.findByText('Miscut')).toBeVisible()
    expect(screen.queryByText('miscut')).toBeNull()
  })

  it('sends a cleared note as no note, not as an empty one', async () => {
    // Adding a row with the box untouched already sent null; clearing a note
    // sent "". Same thing to a person, two different rows in the database.
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
    await user.tab()

    expect(api.setItemErrors).toHaveBeenCalledWith(
      12,
      [{ error_type: 'miscut', details: null }],
      { acknowledgeForSale: false },
    )
  })

  it('shows the message and keeps the rows when a save fails', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    api.setItemErrors.mockRejectedValue(new Error('server is unhappy'))
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
      strict: true,
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

  it('reports a cleared note as no note, the same as the saving mode sends', async () => {
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

    await user.clear(screen.getByLabelText('Miscut details'))

    expect(onChange).toHaveBeenLastCalledWith([{ error_type: 'miscut', details: null }])
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

describe('ErrorsPanel, an item that is for sale', () => {
  it('warns, then sends the acknowledgement on every later save', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(
      <ErrorsPanel
        itemId={12}
        kind="currency"
        saleState={[{ kind: 'listing', id: 3, text: 'listing #3 at 120.00' }]}
      />,
      // StrictMode: this panel already lost an effect guard to the mismatch
      // between how it is tested and how the console actually runs.
      { reference: vocabularies, strict: true },
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('listing #3 at 120.00')
    await user.click(screen.getByLabelText('Record it anyway'))

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.click(screen.getByRole('button', { name: 'Add error' }))
    expect(api.setItemErrors).toHaveBeenLastCalledWith(
      12,
      [{ error_type: 'miscut', details: null }],
      { acknowledgeForSale: true },
    )

    // Sticky: the second change does not ask again.
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'inverted_overprint',
    )
    await user.click(screen.getByRole('button', { name: 'Add error' }))
    expect(api.setItemErrors).toHaveBeenLastCalledWith(12, expect.anything(), {
      acknowledgeForSale: true,
    })
  })

  it('renders no warning for an item that is not for sale', async () => {
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
