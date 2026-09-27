import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    createInventoryItem: vi.fn(),
    suggestNote: vi.fn(),
    suggestCoin: vi.fn(),
    suggestDraftDescription: vi.fn(),
    setItemErrors: vi.fn(),
  },
}))

import { api } from '../../api'
import { COIN_ONLY_FIELDS } from '../../../shared/kinds'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import NewItemForm from './NewItemForm'
import { withSuggestions } from './suggestions'

beforeEach(() => {
  vi.clearAllMocks()
  api.suggestNote.mockResolvedValue({})
  api.suggestCoin.mockResolvedValue({})
})

async function fillTitle(user, text) {
  await user.type(screen.getByRole('textbox', { name: /title/i }), text)
}

describe('NewItemForm: a coin', () => {
  it('submits the coin block and omits currency detail', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 1, item_code: 'CC-000001' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await fillTitle(user, '1878 Morgan dollar')
    await user.type(screen.getByLabelText('mint'), 'cc')
    await user.type(screen.getByRole('textbox', { name: /variety/i }), 'VAM-1')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({
        purchase_order_id: 7,
        item_kind: 'coin',
        source_title: '1878 Morgan dollar',
        mint: 'cc',
        variety: 'VAM-1',
      }),
    )
    const sent = api.createInventoryItem.mock.calls[0][0]
    expect(sent).not.toHaveProperty('serial_number')
    expect(sent).not.toHaveProperty('note_type')
  })
})

describe("NewItemForm: the seller's item id", () => {
  it('is sent, and kept for the next piece of the same listing', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 6, item_code: 'CC-000006' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(
      screen.getByRole('textbox', { name: /seller's item id/i }),
      ' 375454001960 ',
    )
    await fillTitle(user, 'First piece')
    await user.click(screen.getByRole('button', { name: /save and add another/i }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({ sellers_item_id: '375454001960' }),
    )
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /title/i })).toHaveValue(''),
    )
    expect(screen.getByRole('textbox', { name: /seller's item id/i })).toHaveValue(
      ' 375454001960 ',
    )
  })
})

describe('NewItemForm: a set', () => {
  it("sends the set's form, which a note never shows", async () => {
    // "Mixed Sets" is a set form, not a denomination: a denomination is one
    // face value (owner, 2026-09-25).
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 5, item_code: 'CC-000005' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByLabelText('set_form'), 'mixed_sets')
    await fillTitle(user, '9 set lot')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({ set_form: 'mixed_sets' }),
    )

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    expect(screen.queryByLabelText('set_form')).toBeNull()
  })
})

describe('NewItemForm: a banknote', () => {
  async function openAsCurrency(user) {
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
  }

  it('shows banknote fields only once the kind is currency', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    expect(screen.queryByRole('textbox', { name: /serial number/i })).toBeNull()
    expect(screen.getByRole('textbox', { name: /variety/i })).toBeInTheDocument()

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')

    expect(screen.getByRole('textbox', { name: /serial number/i })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /variety/i })).toBeNull()
  })

  // The entry form asks `fieldFitsKind` which fields are a coin's rather than
  // listing them itself, so this form and the item editor read one set
  // (`COIN_ONLY_FIELDS`). Every name in that set is checked, not a sample:
  // the drift being guarded against -- the editor keeping a metal box this
  // form had dropped -- was exactly one field going its own way.
  it("offers none of a coin's own fields once the kind is currency", async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    for (const field of ['strike_type', 'metal', 'mint']) {
      expect(screen.getByLabelText(field)).toBeInTheDocument()
    }

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')

    for (const field of COIN_ONLY_FIELDS) {
      expect(screen.queryByLabelText(field)).toBeNull()
    }
  })

  it('asks a note for its series year only, and sends no other year', async () => {
    // Two year boxes on a note is where 1935 went into Year and the series
    // year stayed blank (CC-007663). The server sets a note's year from its
    // series year.
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 3, item_code: 'CC-000003' })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1881')
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')

    expect(screen.queryByRole('spinbutton', { name: 'Year' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: /range of years/i })).toBeNull()
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '1935')
    await fillTitle(user, 'A 1935 dollar')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    const sent = api.createInventoryItem.mock.calls[0][0]
    expect(sent.series_year).toBe(1935)
    // The 1881 typed while it was a coin is not sent for the note.
    expect(sent).not.toHaveProperty('year_start')
    expect(sent).not.toHaveProperty('year_end')
  })

  it("sends a note's plates and where it was printed", async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 4, item_code: 'CC-000004' })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.type(screen.getByRole('textbox', { name: /face plate/i }), 'FW E82')
    await user.type(screen.getByRole('textbox', { name: /back plate/i }), '1234')
    await user.selectOptions(
      screen.getByRole('combobox', { name: /printed at/i }),
      'fw',
    )
    await fillTitle(user, 'A Fort Worth note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({
        face_plate_number: 'FW E82',
        back_plate_number: '1234',
        printing_facility: 'fw',
      }),
    )
  })

  it('submits the currency block and omits coin detail', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 2, item_code: 'CC-000002' })
    await openAsCurrency(user)

    await fillTitle(user, 'Series 1957 silver certificate')
    await user.type(
      screen.getByRole('textbox', { name: /serial number/i }),
      'A12345678B',
    )
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '1957')
    await user.type(screen.getByRole('textbox', { name: /series letter/i }), 'A')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({
        purchase_order_id: 9,
        item_kind: 'currency',
        serial_number: 'A12345678B',
        series_year: 1957,
        series_letter: 'A',
      }),
    )
    const sent = api.createInventoryItem.mock.calls[0][0]
    expect(sent).not.toHaveProperty('mint')
    expect(sent).not.toHaveProperty('variety')
    expect(sent).not.toHaveProperty('metal')
  })
})

describe('NewItemForm money validation', () => {
  it('refuses to submit a malformed cost, keeping what was typed', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await fillTitle(user, 'Bad cost')
    await user.type(screen.getByRole('textbox', { name: /item cost/i }), 'abc')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(
      await screen.findByText(/item cost must be a money amount/i),
    ).toBeInTheDocument()
    expect(api.createInventoryItem).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: /item cost/i })).toHaveValue('abc')
  })

  it('accepts a well-formed cost', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 3 })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await fillTitle(user, 'Good cost')
    await user.type(screen.getByRole('textbox', { name: /item cost/i }), '12.34')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({ item_cost: '12.34' }),
    )
  })
})

describe('NewItemForm: tax defaults from the purchase', () => {
  it('sends the purchase-wide tax defaults untouched', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 4 })
    render(
      <NewItemForm
        purchaseOrderId={7}
        defaults={{ tax_rate: '0', tax_includes_shipping: true }}
        onSaved={vi.fn()}
      />,
    )
    await fillTitle(user, 'Untaxed item')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem).toHaveBeenCalledWith(
      expect.objectContaining({ tax_rate: '0', tax_includes_shipping: true }),
    )
  })
})

describe('NewItemForm: Save and add another', () => {
  it('keeps the shared fields and clears the per-note ones, then focuses the serial number', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 5, item_code: 'CC-000005' })
    const onSaved = vi.fn()
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={onSaved} />)

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.click(screen.getByRole('radio', { name: 'Received' }))
    await user.type(screen.getByLabelText('denomination'), 'usd_note_1_00')
    await user.type(screen.getByLabelText('series'), 'series_1957')
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '1957')
    await user.type(screen.getByRole('textbox', { name: /series letter/i }), 'B')
    await user.type(screen.getByLabelText('seal_color'), 'blue')
    await user.type(screen.getByLabelText('fed_district'), 'ny')
    await user.type(screen.getByLabelText('note_type'), 'silver_certificate')
    await user.type(screen.getByLabelText('grade'), 'N64')
    await fillTitle(user, 'Note one')
    await user.type(screen.getByRole('textbox', { name: /serial number/i }), 'A1')
    await user.type(screen.getByRole('textbox', { name: /item cost/i }), '5.00')
    await user.type(
      screen.getByRole('textbox', { name: /certificate number/i }),
      'CERT1',
    )

    await user.click(screen.getByRole('button', { name: /save and add another/i }))

    expect(onSaved).toHaveBeenCalledWith({ id: 5, item_code: 'CC-000005' })

    // Shared fields kept, per the controller's exact list -- status and
    // series year among them.
    expect(screen.getByLabelText('item_kind')).toHaveValue('currency')
    expect(screen.getByRole('radio', { name: 'Received' })).toBeChecked()
    expect(screen.getByLabelText('country')).toHaveValue('US')
    expect(screen.getByLabelText('denomination')).toHaveValue('usd_note_1_00')
    expect(screen.getByLabelText('series')).toHaveValue('series_1957')
    expect(screen.getByRole('spinbutton', { name: /series year/i })).toHaveValue(1957)
    expect(screen.getByRole('textbox', { name: /series letter/i })).toHaveValue('B')
    expect(screen.getByLabelText('seal_color')).toHaveValue('blue')
    expect(screen.getByLabelText('fed_district')).toHaveValue('ny')
    expect(screen.getByLabelText('note_type')).toHaveValue('silver_certificate')

    // Per-note fields cleared -- grade and serial number among them, not kept.
    const title = screen.getByRole('textbox', { name: /title/i })
    expect(title).toHaveValue('')
    expect(screen.getByLabelText('grade')).toHaveValue('')
    expect(screen.getByRole('textbox', { name: /serial number/i })).toHaveValue('')
    expect(screen.getByRole('textbox', { name: /item cost/i })).toHaveValue('')
    expect(screen.getByRole('textbox', { name: /certificate number/i })).toHaveValue('')

    // The next note of the lot shares its series and face value, so entry
    // starts again at what differs: its serial number.
    expect(document.activeElement).toBe(
      screen.getByRole('textbox', { name: /serial number/i }),
    )
  })

  it('starts a coin again at its year', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 7, item_code: 'CC-000007' })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1942')
    await fillTitle(user, 'A dime')

    await user.click(screen.getByRole('button', { name: /save and add another/i }))

    const year = screen.getByRole('spinbutton', { name: 'Year' })
    expect(year).toHaveValue(null)
    expect(document.activeElement).toBe(year)
  })

  it('plain Save clears the whole form', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 6 })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)

    await user.clear(screen.getByLabelText('country'))
    await user.type(screen.getByLabelText('country'), 'CA')
    await fillTitle(user, 'Note one')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    // Back to the default, not to blank.
    expect(screen.getByLabelText('country')).toHaveValue('US')
    expect(screen.getByRole('textbox', { name: /title/i })).toHaveValue('')
  })
})

describe('NewItemForm: changing kind clears grade', () => {
  it('clears a grade entered under one scale when the kind changes', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await user.type(screen.getByLabelText('grade'), 'MS64')
    expect(screen.getByLabelText('grade')).toHaveValue('MS64')

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')

    expect(screen.getByLabelText('grade')).toHaveValue('')
  })

  it('keeps the grade when both kinds read the same scale', async () => {
    // coin, bullion and medal all grade on the coin scale. Only the currency
    // boundary makes a grade meaningless, so only it clears one.
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await user.type(screen.getByLabelText('grade'), 'MS64')

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'bullion')

    expect(screen.getByLabelText('grade')).toHaveValue('MS64')
  })
})

describe('NewItemForm: denomination choices', () => {
  // One denomination from each side, as the reference context holds them.
  const vocabularies = emptyReference({
    tables: {
      denomination: [
        {
          code: 'usd_note_1_00',
          label: '$1 Bill',
          source: 'seeded',
          extra: { kind: 'note' },
        },
        {
          code: 'usd_coin_0_25',
          label: 'Quarter',
          source: 'seeded',
          extra: { kind: 'coin' },
        },
      ],
    },
  })

  it("offers a coin's denomination picker Quarter and not $1 Bill", async () => {
    renderWithProviders(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
      { reference: vocabularies },
    )
    const select = screen.getByRole('combobox', { name: 'denomination' })
    const options = Array.from(select.querySelectorAll('option')).map(
      (o) => o.textContent,
    )
    expect(options).toContain('Quarter')
    expect(options).not.toContain('$1 Bill')
  })
})

describe('NewItemForm: a range with no Year from', () => {
  it('refuses a Year to with an empty Year from, rather than dropping it', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await fillTitle(user, 'Ranged item with no start')
    await user.click(screen.getByRole('checkbox', { name: /range of years/i }))
    await user.type(screen.getByLabelText(/year to/i), '1925')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(
      await screen.findByText(/year from.*year to|enter.*year from/i),
    ).toBeInTheDocument()
    expect(api.createInventoryItem).not.toHaveBeenCalled()
  })
})

describe('NewItemForm: disabled while the purchase-wide tax rate is invalid', () => {
  it('disables both Save buttons and shows the reason', () => {
    render(
      <NewItemForm
        purchaseOrderId={7}
        defaults={{}}
        onSaved={vi.fn()}
        disabledReason="Fix the tax rate above before saving items."
      />,
    )

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /save and add another/i })).toBeDisabled()
    expect(
      screen.getByText(/fix the tax rate above before saving items/i),
    ).toBeInTheDocument()
  })
})

describe('NewItemForm keyboard accelerators', () => {
  it('gives the key fields and Save and add another an access key, shown in their label', () => {
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    const expected = [
      [screen.getByLabelText('item_kind'), 'k'],
      [screen.getByRole('textbox', { name: /title/i }), 't'],
      [screen.getByRole('button', { name: /save and add another/i }), 'n'],
    ]
    for (const [element, key] of expected) {
      expect(element).toHaveAttribute('accesskey', key)
      expect(element).toHaveAttribute('aria-keyshortcuts', `Alt+${key.toUpperCase()}`)
    }
  })

  it('saves with Ctrl+S, which the Save button names', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 14, item_code: 'CC-000014' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toHaveAttribute('aria-keyshortcuts', 'Control+S')
    expect(save).not.toHaveAttribute('accesskey')

    await fillTitle(user, 'A dime')
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })

    await waitFor(() => expect(api.createInventoryItem).toHaveBeenCalledTimes(1))
  })

  it('never uses D, E or F as an accelerator', () => {
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    const banned = ['d', 'e', 'f']
    document.querySelectorAll('[accesskey]').forEach((el) => {
      expect(banned).not.toContain(el.getAttribute('accesskey').toLowerCase())
    })
  })
})

describe('NewItemForm suggestions from the facts', () => {
  const FOUND = {
    note_type: 'silver_certificate',
    seal_color: 'blue',
    signature_combination: 'priest_anderson',
    fed_district: null,
  }

  async function openNote(user) {
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.type(screen.getByLabelText('denomination'), 'usd_note_1')
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '1957')
  }

  it('fills what the facts decide and marks it as a suggestion', async () => {
    const user = userEvent.setup()
    api.suggestNote.mockResolvedValue(FOUND)
    await openNote(user)

    await waitFor(() =>
      expect(screen.getByLabelText('note_type')).toHaveValue('silver_certificate'),
    )
    expect(api.suggestNote).toHaveBeenLastCalledWith(
      expect.objectContaining({ denomination: 'usd_note_1', series_year: '1957' }),
    )
    expect(screen.getByLabelText('seal_color')).toHaveValue('blue')
    expect(screen.getAllByText('suggested')).toHaveLength(3)

    api.createInventoryItem.mockResolvedValue({ id: 8 })
    await fillTitle(user, 'A 1957 dollar')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const sent = api.createInventoryItem.mock.calls[0][0]
    expect(sent.note_type).toBe('silver_certificate')
    expect(new Set(sent.suggested)).toEqual(
      new Set(['note_type', 'seal_color', 'signature_combination']),
    )
  })

  it('treats a changed suggestion as the persons own', async () => {
    const user = userEvent.setup()
    api.suggestNote.mockResolvedValue(FOUND)
    await openNote(user)
    await waitFor(() => expect(screen.getByLabelText('seal_color')).toHaveValue('blue'))

    await user.clear(screen.getByLabelText('seal_color'))
    await user.type(screen.getByLabelText('seal_color'), 'red')

    // The person's pick is sent with the facts, and not replaced by them.
    await waitFor(() =>
      expect(api.suggestNote).toHaveBeenLastCalledWith(
        expect.objectContaining({ seal_color: 'red' }),
      ),
    )
    expect(screen.getByLabelText('seal_color')).toHaveValue('red')
    api.createInventoryItem.mockResolvedValue({ id: 9 })
    await fillTitle(user, 'A red seal')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const sent = api.createInventoryItem.mock.calls[0][0]
    expect(sent.seal_color).toBe('red')
    expect(sent.suggested).not.toContain('seal_color')
  })

  it('takes its suggestions back when the denomination is cleared', async () => {
    const user = userEvent.setup()
    api.suggestNote.mockResolvedValue(FOUND)
    await openNote(user)
    await waitFor(() => expect(screen.getByLabelText('seal_color')).toHaveValue('blue'))

    await user.clear(screen.getByLabelText('denomination'))

    await waitFor(() => expect(screen.getByLabelText('seal_color')).toHaveValue(''))
    expect(screen.queryByText('suggested')).toBeNull()
  })

  it('asks for a coins metal', async () => {
    const user = userEvent.setup()
    api.suggestCoin.mockResolvedValue({ metal: 'silver' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)

    await user.type(screen.getByLabelText('denomination'), 'usd_coin_0_10')
    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1964')

    await waitFor(() => expect(screen.getByLabelText('metal')).toHaveValue('silver'))
    expect(api.suggestNote).not.toHaveBeenCalled()
  })
})

describe('NewItemForm: errors, held in form state until the item exists', () => {
  const errorVocab = emptyReference({
    tables: {
      error_type: [
        {
          code: 'miscut',
          label: 'Miscut',
          source: 'seeded',
          aliases: [],
          extra: { applies_to: 'any' },
        },
      ],
    },
  })

  async function addAnErrorRow(user) {
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.type(screen.getByPlaceholderText('details'), "miscut at 3 o'clock")
    await user.click(screen.getByRole('button', { name: 'Add error' }))
  }

  it('sends the typed errors for the new item once it is created', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 11, item_code: 'CC-000011' })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 11, errors: [] })
    renderWithProviders(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
      {
        reference: errorVocab,
      },
    )

    await addAnErrorRow(user)
    await fillTitle(user, 'A miscut cent')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.setItemErrors).toHaveBeenCalledWith(11, [
        { error_type: 'miscut', details: "miscut at 3 o'clock" },
      ]),
    )
  })

  it('does not call setItemErrors when no error rows were added', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 12, item_code: 'CC-000012' })
    renderWithProviders(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
      {
        reference: errorVocab,
      },
    )

    await fillTitle(user, 'A plain cent')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.createInventoryItem).toHaveBeenCalled())
    expect(api.setItemErrors).not.toHaveBeenCalled()
  })

  it(
    'when the item is created but its errors fail to save, keeps the item code, ' +
      'the typed errors, does not call onSaved, and a Retry that succeeds finishes the save',
    async () => {
      const user = userEvent.setup()
      const onSaved = vi.fn()
      api.createInventoryItem.mockResolvedValue({ id: 13, item_code: 'CC-000013' })
      api.setItemErrors.mockRejectedValueOnce(new Error('boom'))
      renderWithProviders(
        <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={onSaved} />,
        { reference: errorVocab },
      )

      await addAnErrorRow(user)
      await fillTitle(user, 'A miscut cent')
      await user.click(screen.getByRole('button', { name: 'Save' }))

      expect(
        await screen.findByText(
          'CC-000013 was created, but its errors were not saved: boom',
        ),
      ).toBeInTheDocument()
      expect(onSaved).not.toHaveBeenCalled()
      // The form, and the error row just typed, are still on screen.
      expect(screen.getByRole('textbox', { name: /title/i })).toHaveValue(
        'A miscut cent',
      )
      expect(screen.getByText('Miscut')).toBeVisible()
      expect(screen.getByDisplayValue("miscut at 3 o'clock")).toBeVisible()
      // Save and Save-and-add-another are blocked while the failure is
      // pending: the item already exists, and either button would call
      // `createInventoryItem` again and enter the same piece a second time.
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(
        screen.getByRole('button', { name: /save and add another/i }),
      ).toBeDisabled()

      api.setItemErrors.mockResolvedValueOnce({ inventory_item_id: 13, errors: [] })
      await user.click(screen.getByRole('button', { name: 'Retry' }))

      await waitFor(() =>
        expect(api.setItemErrors).toHaveBeenLastCalledWith(13, [
          { error_type: 'miscut', details: "miscut at 3 o'clock" },
        ]),
      )
      await waitFor(() =>
        expect(onSaved).toHaveBeenCalledWith({ id: 13, item_code: 'CC-000013' }),
      )
      expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
      // The form is cleared on a completed save, same as a plain Save.
      expect(screen.getByRole('textbox', { name: /title/i })).toHaveValue('')
    },
  )
})

describe('NewItemForm: what fits a note, and what the facts cannot find', () => {
  const row = (code, extra = {}) => ({
    code,
    label: code,
    source: 'seeded',
    aliases: [],
    extra,
  })
  const vocab = emptyReference({
    tables: {
      item_kind: [row('coin'), row('currency')],
      grade_designation: [
        row('DCAM', { applies_to: 'coin' }),
        row('EPQ', { applies_to: 'currency' }),
      ],
    },
  })
  const optionsOf = (name) =>
    [...screen.getByRole('combobox', { name }).options].map((o) => o.value)

  it('offers a note only the note designations, and clears a coin one', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />,
      { reference: vocab },
    )
    expect(optionsOf('grade_designation')).toContain('DCAM')
    expect(optionsOf('grade_designation')).not.toContain('EPQ')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'grade_designation' }),
      'DCAM',
    )

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_kind' }),
      'currency',
    )

    expect(optionsOf('grade_designation')).toContain('EPQ')
    expect(optionsOf('grade_designation')).not.toContain('DCAM')
    // DCAM means nothing on a banknote: it is not carried across.
    expect(screen.getByRole('combobox', { name: 'grade_designation' })).toHaveValue('')
  })

  it('says when no issue matches the series, and fills nothing with it', async () => {
    const user = userEvent.setup()
    const warning =
      'No $2 note of Series 1953E is on record. ' +
      'Series 1953 on record: 1953, 1953A, 1953B, 1953C.'
    api.suggestNote.mockResolvedValue({
      note_type: null,
      seal_color: null,
      signature_combination: null,
      fed_district: null,
      warning,
    })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.type(screen.getByLabelText('denomination'), 'usd_note_2')
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '1953')
    await user.type(screen.getByRole('textbox', { name: /series letter/i }), 'E')

    expect(await screen.findByRole('status')).toHaveTextContent(warning)
    expect(screen.queryByText('suggested')).toBeNull()

    // A different letter is a different question: the old answer is not
    // shown for it.
    api.suggestNote.mockResolvedValue({ signature_combination: 'smith_dillon' })
    await user.clear(screen.getByRole('textbox', { name: /series letter/i }))
    await user.type(screen.getByRole('textbox', { name: /series letter/i }), 'B')
    await waitFor(() =>
      expect(screen.getByLabelText('signature_combination')).toHaveValue(
        'smith_dillon',
      ),
    )
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('NewItemForm: Suggest description', () => {
  it('asks with what is entered and puts the answer in the box', async () => {
    const user = userEvent.setup()
    api.suggestDraftDescription.mockResolvedValue({
      description: 'Choice Unc 64 Trinary 2017A $1 S/N B12211221A.',
    })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.type(screen.getByLabelText('denomination'), 'usd_note_1')
    await user.type(screen.getByRole('spinbutton', { name: /series year/i }), '2017')
    await user.type(screen.getByRole('textbox', { name: /series letter/i }), 'A')
    await user.type(
      screen.getByRole('textbox', { name: /serial number/i }),
      'B12211221A',
    )

    await user.click(screen.getByRole('button', { name: 'Suggest description' }))

    expect(api.suggestDraftDescription).toHaveBeenCalledWith(
      expect.objectContaining({
        item_kind: 'currency',
        denomination: 'usd_note_1',
        series_year: 2017,
        series_letter: 'A',
        serial_number: 'B12211221A',
        piece_count: 1,
        errors: [],
      }),
    )
    const sent = api.suggestDraftDescription.mock.calls[0][0]
    expect(sent).not.toHaveProperty('mint')
    expect(screen.getByRole('textbox', { name: /description/i })).toHaveValue(
      'Choice Unc 64 Trinary 2017A $1 S/N B12211221A.',
    )
    expect(screen.getByText(/edit it before saving/)).toBeInTheDocument()
  })

  it('leaves the box alone when there is nothing to describe yet', async () => {
    const user = userEvent.setup()
    api.suggestDraftDescription.mockResolvedValue({ description: '' })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByRole('textbox', { name: /description/i }), 'mine')

    await user.click(screen.getByRole('button', { name: 'Suggest description' }))

    expect(await screen.findByText(/Nothing entered yet/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /description/i })).toHaveValue('mine')
  })
})

describe('withSuggestions', () => {
  const blank = { note_type: '', seal_color: '', fed_district: '' }

  it('fills empty fields and marks them', () => {
    const next = withSuggestions(
      { form: blank, suggested: {} },
      { note_type: 'frn', seal_color: null },
    )
    expect(next.form.note_type).toBe('frn')
    expect(next.suggested).toEqual({ note_type: 'frn' })
  })

  it('never replaces a value the person picked', () => {
    const next = withSuggestions(
      { form: { ...blank, seal_color: 'red' }, suggested: {} },
      { seal_color: 'blue' },
    )
    expect(next.form.seal_color).toBe('red')
    expect(next.suggested).toEqual({})
  })

  it('replaces or clears an earlier suggestion when the facts change', () => {
    const before = {
      form: { ...blank, note_type: 'frn', fed_district: 'B' },
      suggested: { note_type: 'frn', fed_district: 'B' },
    }
    const next = withSuggestions(before, {
      note_type: 'silver_certificate',
      fed_district: null,
    })
    expect(next.form).toEqual({ ...blank, note_type: 'silver_certificate' })
    expect(next.suggested).toEqual({ note_type: 'silver_certificate' })
  })
})

//: The `data-help` keys in the order the form shows them.
function fieldOrder(container) {
  return [...container.querySelectorAll('[data-help]')].map((el) =>
    el.getAttribute('data-help'),
  )
}

function isInOrder(keys, container) {
  const shown = fieldOrder(container)
  const at = keys.map((key) => shown.indexOf(key))
  return at.every((i) => i >= 0) && at.every((i, n) => n === 0 || i > at[n - 1])
}

describe('NewItemForm: the facts first', () => {
  it('asks a coin for its year, mint and denomination right after its kind', () => {
    const { container } = render(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
    )
    expect(fieldOrder(container).slice(4, 8)).toEqual([
      'item_kind',
      'year_start',
      'mint',
      'denomination',
    ])
    expect(
      isInOrder(
        ['denomination', 'series', 'metal', 'country', 'grade', 'source_title'],
        container,
      ),
    ).toBe(true)
  })

  it('asks a note for its series, face value, serial and plates right after its kind', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
    )
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')

    expect(fieldOrder(container).slice(4, 12)).toEqual([
      'item_kind',
      'series_year',
      'series_letter',
      'denomination',
      'serial_number',
      'face_plate_number',
      'back_plate_number',
      'printing_facility',
    ])
    expect(
      isInOrder(
        [
          'printing_facility',
          'series',
          'note_type',
          'fed_district',
          'country',
          'grade',
          'source_title',
        ],
        container,
      ),
    ).toBe(true)
  })

  it('fills the design series the facts decide, and sends it as a suggestion', async () => {
    const user = userEvent.setup()
    api.suggestCoin.mockResolvedValue({
      metal: 'silver',
      series: 'winged_liberty_head_dime',
    })
    api.createInventoryItem.mockResolvedValue({ id: 8, item_code: 'CC-000008' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1942')
    await user.type(screen.getByLabelText('denomination'), 'usd_coin_0_10')

    await waitFor(() =>
      expect(screen.getByLabelText('series')).toHaveValue('winged_liberty_head_dime'),
    )
    // The series is asked for, never sent as a fact to decide it by.
    expect(api.suggestCoin.mock.calls.at(-1)[0]).not.toHaveProperty('series')

    await fillTitle(user, 'A dime')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.createInventoryItem.mock.calls[0][0].suggested).toEqual(
      expect.arrayContaining(['series', 'metal']),
    )
  })
})

describe('NewItemForm: the country', () => {
  it('starts at the United States and is sent without being touched', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 9, item_code: 'CC-000009' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    expect(screen.getByLabelText('country')).toHaveValue('US')

    await fillTitle(user, 'A dime')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem.mock.calls[0][0].country).toBe('US')
  })

  it('can be changed, or emptied for a piece of unknown origin', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 10, item_code: 'CC-000010' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.clear(screen.getByLabelText('country'))
    await fillTitle(user, 'A token')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem.mock.calls[0][0]).not.toHaveProperty('country')
  })

  it('lets the coin look-up know the country from the start', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1942')
    await user.type(screen.getByLabelText('denomination'), 'usd_coin_0_10')

    await waitFor(() =>
      expect(api.suggestCoin).toHaveBeenLastCalledWith(
        expect.objectContaining({ country: 'US', year: '1942' }),
      ),
    )
  })
})

describe('NewItemForm: attributes', () => {
  const ATTRIBUTES = [
    {
      code: 'binary',
      label: 'Binary',
      source: 'seeded',
      aliases: [],
      extra: { applies_to: 'currency' },
    },
    {
      code: 'star',
      label: 'Star',
      source: 'seeded',
      aliases: [],
      extra: { applies_to: 'currency' },
    },
    {
      code: 'cac',
      label: 'CAC',
      source: 'seeded',
      aliases: [],
      extra: { applies_to: 'coin' },
    },
  ]

  async function openNote(user) {
    renderWithProviders(
      <NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />,
      {
        reference: emptyReference({ tables: { item_attribute: ATTRIBUTES } }),
      },
    )
    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
  }

  it('designates a bill Binary and sends it with the item', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 11, item_code: 'CC-000011' })
    await openNote(user)

    await user.selectOptions(screen.getByLabelText('item_attribute'), 'binary')
    expect(screen.getByRole('button', { name: 'Remove Binary' })).toBeInTheDocument()
    await fillTitle(user, 'Binary note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(api.createInventoryItem.mock.calls[0][0].attributes).toEqual(['binary'])
  })

  it("offers a note only a note's attributes", async () => {
    const user = userEvent.setup()
    await openNote(user)
    const offered = within(screen.getByLabelText('item_attribute'))
      .getAllByRole('option')
      .map((o) => o.value)
    expect(offered).toContain('binary')
    expect(offered).not.toContain('cac')
  })

  it("drops a note's attributes when the piece becomes a coin", async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 12, item_code: 'CC-000012' })
    await openNote(user)
    await user.selectOptions(screen.getByLabelText('item_attribute'), 'binary')

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'coin')

    expect(screen.queryByRole('button', { name: 'Remove Binary' })).toBeNull()
    await fillTitle(user, 'A coin')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.createInventoryItem.mock.calls[0][0]).not.toHaveProperty('attributes')
  })

  it('clears them for the next piece, like a serial number', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 13, item_code: 'CC-000013' })
    await openNote(user)
    await user.selectOptions(screen.getByLabelText('item_attribute'), 'binary')
    await fillTitle(user, 'Binary note')

    await user.click(screen.getByRole('button', { name: /save and add another/i }))

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Remove Binary' })).toBeNull(),
    )
  })
})

describe('NewItemForm: the listing web address', () => {
  const LISTING = 'https://www.hibid.com/lot/307575524/1898-morgan-dollar'

  it('is sent trimmed, and kept for the next piece of the same listing', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 15, item_code: 'CC-000015' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(
      screen.getByRole('textbox', { name: /listing web address/i }),
      ` ${LISTING} `,
    )
    await fillTitle(user, 'Lot piece')
    await user.click(screen.getByRole('button', { name: /save and add another/i }))

    expect(api.createInventoryItem.mock.calls[0][0].listing_url).toBe(LISTING)
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /title/i })).toHaveValue(''),
    )
    // Kept for the next piece (a URL box drops the surrounding spaces itself).
    expect(screen.getByRole('textbox', { name: /listing web address/i })).toHaveValue(
      LISTING,
    )
  })

  it('is not sent when left blank', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 16, item_code: 'CC-000016' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await fillTitle(user, 'No link')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.createInventoryItem.mock.calls[0][0]).not.toHaveProperty('listing_url')
  })
})

describe('NewItemForm: the listing first', () => {
  const EBAY = 'https://www.ebay.com/itm/126845170680'

  function sellersId() {
    return screen.getByRole('textbox', { name: /seller's item id/i })
  }
  function listing() {
    return screen.getByRole('textbox', { name: /listing web address/i })
  }

  it('starts with the listing, its id, the price and the shipping', () => {
    const { container } = render(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
    )
    expect(fieldOrder(container).slice(0, 5)).toEqual([
      'listing_url',
      'sellers_item_id',
      'item_cost',
      'shipping_cost',
      'item_kind',
    ])
  })

  it("suggests the seller's item id the listing's address carries", async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 17, item_code: 'CC-000017' })
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(listing(), EBAY)

    expect(sellersId()).toHaveValue('126845170680')
    expect(
      within(sellersId().closest('label')).getByText('suggested'),
    ).toBeInTheDocument()

    await fillTitle(user, 'A dime')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.createInventoryItem.mock.calls[0][0]).toMatchObject({
      listing_url: EBAY,
      sellers_item_id: '126845170680',
    })
  })

  it('leaves an id the person typed alone', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(sellersId(), 'MY-ID')
    await user.type(listing(), EBAY)

    expect(sellersId()).toHaveValue('MY-ID')
  })

  it("makes a suggested id the person's once they change it", async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(listing(), EBAY)
    await user.type(sellersId(), '9')

    expect(sellersId()).toHaveValue('1268451706809')
    expect(within(sellersId().closest('label')).queryByText('suggested')).toBeNull()
    await user.clear(listing())
    expect(sellersId()).toHaveValue('1268451706809')
  })

  it('takes back its suggestion when the address no longer carries an id', async () => {
    const user = userEvent.setup()
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    await user.type(listing(), EBAY)
    await user.clear(listing())
    await user.type(listing(), 'https://www.whatnot.com/order/YLMRqwXPqcdsXx35UxxA2V')

    expect(sellersId()).toHaveValue('')
  })
})

describe("NewItemForm: a lot's page is its purchase's", () => {
  const LOT = 'https://hibid.com/lot/280623476/1986-2024-american-eagle'

  function listing() {
    return screen.getByRole('textbox', { name: /listing web address/i })
  }

  it("starts with the purchase's address and the id it carries, both suggested", () => {
    render(
      <NewItemForm
        purchaseOrderId={7}
        orderUrl={LOT}
        defaults={{}}
        onSaved={vi.fn()}
      />,
    )
    expect(listing()).toHaveValue(LOT)
    expect(
      within(listing().closest('label')).getByText('suggested'),
    ).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /seller's item id/i })).toHaveValue(
      '280623476',
    )
  })

  it('offers it again after a plain Save', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 18, item_code: 'CC-000018' })
    render(
      <NewItemForm
        purchaseOrderId={7}
        orderUrl={LOT}
        defaults={{}}
        onSaved={vi.fn()}
      />,
    )
    await user.clear(listing())
    await user.type(listing(), 'https://hibid.com/lot/99/other')
    await fillTitle(user, 'Another lot')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(listing()).toHaveValue(LOT))
  })

  it('starts empty on a purchase with no such address', () => {
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    expect(listing()).toHaveValue('')
  })
})

describe('NewItemForm: the description last', () => {
  it('asks for errors before the description, and the status after it', () => {
    const { container } = render(
      <NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />,
    )
    const order = fieldOrder(container)
    const at = (key) => order.indexOf(key)
    // Suggest description reads the errors, so they are entered first.
    expect(at('errors')).toBeGreaterThan(at('source_title'))
    expect(at('description')).toBeGreaterThan(at('errors'))
    expect(at('suggest_description')).toBeGreaterThan(at('description'))
    expect(at('new_item_status')).toBeGreaterThan(at('suggest_description'))
    expect(order.at(-1)).toBe('new_item_status')
  })
})
