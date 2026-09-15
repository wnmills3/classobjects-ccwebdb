import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    createInventoryItem: vi.fn(),
  },
}))

import { api } from '../../api'
import NewItemForm from './NewItemForm'

beforeEach(() => {
  vi.clearAllMocks()
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
  it('keeps the shared fields and clears the per-note ones, then focuses Title', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 5, item_code: 'CC-000005' })
    const onSaved = vi.fn()
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={onSaved} />)

    await user.clear(screen.getByLabelText('item_kind'))
    await user.type(screen.getByLabelText('item_kind'), 'currency')
    await user.click(screen.getByRole('radio', { name: 'Received' }))
    await user.type(screen.getByLabelText('country'), 'US')
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

    // Focus moves to Title for the next entry.
    expect(document.activeElement).toBe(title)
  })

  it('plain Save clears the whole form', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockResolvedValue({ id: 6 })
    render(<NewItemForm purchaseOrderId={9} defaults={{}} onSaved={vi.fn()} />)

    await user.type(screen.getByLabelText('country'), 'US')
    await fillTitle(user, 'Note one')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByLabelText('country')).toHaveValue('')
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
  it('gives the key fields and both save buttons an access key, shown in their label', () => {
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    const expected = [
      [screen.getByLabelText('item_kind'), 'k'],
      [screen.getByRole('textbox', { name: /title/i }), 't'],
      [screen.getByRole('button', { name: 'Save' }), 'v'],
      [screen.getByRole('button', { name: /save and add another/i }), 'n'],
    ]
    for (const [element, key] of expected) {
      expect(element).toHaveAttribute('accesskey', key)
      expect(element).toHaveAttribute('aria-keyshortcuts', `Alt+${key.toUpperCase()}`)
    }
  })

  it('never uses D, E or F as an accelerator', () => {
    render(<NewItemForm purchaseOrderId={7} defaults={{}} onSaved={vi.fn()} />)
    const banned = ['d', 'e', 'f']
    document.querySelectorAll('[accesskey]').forEach((el) => {
      expect(banned).not.toContain(el.getAttribute('accesskey').toLowerCase())
    })
  })
})
