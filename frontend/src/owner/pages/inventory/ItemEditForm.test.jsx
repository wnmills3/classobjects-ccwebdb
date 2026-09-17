import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
  },
}))

import { api } from '../../api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import ItemEditForm from './ItemEditForm'

const item = {
  id: 12,
  item_code: 'C-012',
  description: 'Mercury Dime',
  reviewed: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getInventoryItem.mockResolvedValue(item)
  api.setItemReview.mockResolvedValue({ reviewed: ['description'] })
})

describe('ItemEditForm', () => {
  it('loads the item it was given', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(api.getInventoryItem).toHaveBeenCalledWith(12)
  })

  it('offers a confirmed checkbox per reviewable field', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.getAllByLabelText(/confirmed/i).length).toBeGreaterThan(0)
  })

  it('records a field as reviewed when its box is ticked', async () => {
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    // By role rather than by label: the label's title also contains
    // "confirmed", so a text query can return the label instead of the input.
    // By name as well: "Range of years" is a checkbox too, ahead of these.
    const box = screen.getAllByRole('checkbox', { name: /confirmed/ })[0]
    expect(box).not.toBeChecked()
    await user.click(box)

    await waitFor(() =>
      // replace:true -- unticking must remove the record, not leave a
      // confirmation nobody stands behind.
      expect(api.setItemReview).toHaveBeenCalledWith(12, expect.any(Array), true),
    )
  })

  it('reports a failed load rather than showing an empty form', async () => {
    api.getInventoryItem.mockRejectedValue(new Error('item 12 is gone'))
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText('item 12 is gone')).toBeInTheDocument()
  })
})

describe('No sales tax charged', () => {
  const taxed = {
    ...item,
    version: 3,
    item_cost: '179.00',
    shipping_cost: '0.00',
    sales_tax: '11.37',
    total_cost: '190.37',
    tax_rate: '0.0635',
    tax_includes_shipping: true,
    default_tax_rate: '0.0635',
  }
  const untaxed = {
    ...taxed,
    sales_tax: '0.00',
    total_cost: '179.00',
    tax_rate: '0.0000',
  }

  async function saveAfterClicking(loaded) {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue(loaded)
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await user.click(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    )
    await user.click(screen.getByRole('button', { name: /save/i }))
  }

  it('sets the rate to zero when ticked', async () => {
    await saveAfterClicking(taxed)
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0', version: 3 }),
      ),
    )
  })

  it('shows an item already recorded as untaxed as ticked', async () => {
    api.getInventoryItem.mockResolvedValue(untaxed)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    ).toBeChecked()
  })

  it('ticking and unticking again leaves the rate as it was', async () => {
    const user = userEvent.setup()
    // Stamped at 6.35% when bought; the setting has since moved to 7%.
    api.getInventoryItem.mockResolvedValue({ ...taxed, default_tax_rate: '0.0700' })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const box = await screen.findByRole('checkbox', { name: /no sales tax charged/i })

    await user.click(box)
    await user.click(box)

    // Back to the rate it was bought at, not today's setting -- so nothing
    // changed and there is nothing to save.
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })

  it('restores the configured rate when unticked, not a rate of its own', async () => {
    await saveAfterClicking({ ...untaxed, default_tax_rate: '0.0700' })
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0.0700' }),
      ),
    )
  })
})

describe('Grade choices', () => {
  // One grade from each scale, as the reference context holds them.
  const vocabularies = emptyReference({
    tables: {
      grade: [
        {
          code: 'MS65',
          label: 'MS-65',
          source: 'seeded',
          extra: { grade_scale: 'sheldon' },
        },
        {
          code: 'UNC',
          label: 'UNC',
          source: 'seeded',
          extra: { grade_scale: 'adjectival' },
        },
        {
          code: 'N64',
          label: 'Choice Uncirculated 64',
          source: 'seeded',
          extra: { grade_scale: 'note' },
        },
      ],
      strike_type: [
        { code: 'business', label: 'Business Strike', source: 'seeded', extra: {} },
        { code: 'proof', label: 'Proof', source: 'seeded', extra: {} },
      ],
    },
  })

  async function gradeOptions(kind) {
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: kind })
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      {
        reference: vocabularies,
      },
    )
    await screen.findByDisplayValue('Mercury Dime')
    const select = screen.getByRole('combobox', { name: 'grade' })
    return Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
  }

  it('offers a note only paper-money grades', async () => {
    const options = await gradeOptions('currency')
    expect(options).toContain('Choice Uncirculated 64')
    expect(options).not.toContain('MS-65')
    expect(options).not.toContain('UNC')
  })

  it('offers a coin only coin grades', async () => {
    const options = await gradeOptions('coin')
    expect(options).toContain('MS-65')
    expect(options).toContain('UNC')
    expect(options).not.toContain('Choice Uncirculated 64')
  })

  // 65 is MS65 or PR65 by its strike type; a note has none to choose.
  it('asks a coin for its strike type', async () => {
    await gradeOptions('coin')
    expect(screen.getByRole('combobox', { name: 'strike_type' })).toBeInTheDocument()
  })

  it('does not ask a note for a strike type', async () => {
    await gradeOptions('currency')
    expect(screen.queryByRole('combobox', { name: 'strike_type' })).toBeNull()
  })
})

describe('ItemEditForm years', () => {
  // A single year is stored as start == end; a range is for a multi-year set
  // or a coin dated only to an era. 13 items in the collection have a range,
  // so the form asks for one year unless the item has, or is given, a range.
  async function open(years) {
    api.getInventoryItem.mockResolvedValue({ ...item, ...years })
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    return user
  }

  const rangeBox = () => screen.getByRole('checkbox', { name: 'Range of years' })

  it('asks for one year when the item has one', async () => {
    await open({ year_start: 1878, year_end: 1878 })
    expect(screen.getByRole('spinbutton', { name: 'Year' })).toHaveValue(1878)
    expect(screen.queryByRole('spinbutton', { name: 'Year to' })).toBeNull()
    expect(rangeBox()).not.toBeChecked()
  })

  it('treats a start year with no end as one year', async () => {
    await open({ year_start: 1881, year_end: null })
    expect(screen.getByRole('spinbutton', { name: 'Year' })).toHaveValue(1881)
    expect(rangeBox()).not.toBeChecked()
  })

  it('opens a stored range as a range, so it cannot be collapsed unseen', async () => {
    await open({ year_start: 1999, year_end: 2008 })
    expect(rangeBox()).toBeChecked()
    expect(screen.getByRole('spinbutton', { name: 'Year from' })).toHaveValue(1999)
    expect(screen.getByRole('spinbutton', { name: 'Year to' })).toHaveValue(2008)
  })

  it('saves one year as both ends', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    await user.type(year, '1964')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_start: '1964', year_end: '1964' }),
      ),
    )
  })

  it('clears both ends when the one year is emptied', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    // Empty, not the stored year shown back: a cleared draft is null.
    expect(year).toHaveValue(null)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_start: null, year_end: null }),
      ),
    )
  })

  it('reveals the end year when Range of years is ticked', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    await user.click(rangeBox())
    const to = screen.getByRole('spinbutton', { name: 'Year to' })
    expect(to).toHaveValue(1878)
    await user.clear(to)
    await user.type(to, '1885')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_end: '1885' }),
      ),
    )
  })

  it('collapses a stored range to its start year when unticked', async () => {
    const user = await open({ year_start: 1999, year_end: 2008 })
    await user.click(rangeBox())
    expect(screen.queryByRole('spinbutton', { name: 'Year to' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_end: 1999 }),
      ),
    )
  })

  it('keeps focus on Range of years while the end year comes and goes', async () => {
    // Found in a real browser: the two layouts were separate branches, so
    // ticking replaced the checkbox itself and a keyboard user lost their place.
    const user = await open({ year_start: 1878, year_end: 1878 })
    const box = rangeBox()
    await user.click(box)
    expect(rangeBox()).toBe(box)
    expect(document.activeElement).toBe(box)
    await user.click(box)
    expect(rangeBox()).toBe(box)
  })

  it('ticking and unticking on a single year is no change at all', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    await user.click(rangeBox())
    await user.click(rangeBox())
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('confirms one year as both ends with one box', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    api.setItemReview.mockResolvedValue({})
    const boxes = screen.getAllByRole('checkbox', { name: /confirmed/ })
    await user.click(boxes[0])
    await waitFor(() =>
      expect(api.setItemReview).toHaveBeenCalledWith(
        12,
        expect.arrayContaining(['year_start', 'year_end']),
        true,
      ),
    )
  })
})

describe('ItemEditForm keyboard accelerators', () => {
  it('gives each field and Save an access key, shown in its label', async () => {
    api.getInventoryItem.mockResolvedValue({
      ...item,
      year_start: 1878,
      year_end: 1878,
    })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    const expected = [
      [screen.getByRole('textbox', { name: /Title/ }), 't'],
      [screen.getByRole('textbox', { name: /Description/ }), 'c'],
      [screen.getByRole('spinbutton', { name: 'Year' }), 'y'],
      [screen.getByRole('checkbox', { name: 'Range of years' }), 'r'],
      [screen.getByRole('button', { name: 'Save' }), 'v'],
    ]
    for (const [element, key] of expected) {
      expect(element).toHaveAttribute('accesskey', key)
      expect(element).toHaveAttribute('aria-keyshortcuts', `Alt+${key.toUpperCase()}`)
    }
  })

  it('saves with Ctrl+S once there is something to save', async () => {
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(api.updateInventoryItem).not.toHaveBeenCalled()

    await user.type(screen.getByRole('textbox', { name: /Description/ }), '!')
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
  })
})

describe('field labels in the grid', () => {
  it('keeps each label whole as one grid item, accelerator letter and all', async () => {
    // `.field` is `display: grid` with four columns, and every direct child
    // is a grid item. A label built from loose text around a `<u>` put each
    // piece of the word in a different column -- the owner saw
    // "T      i      tle" and "Des   c   ription". Asserted here, on the real
    // form, and not only on AccessLabel in isolation: the bug lived in the
    // relationship between the two.
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const title = await screen.findByRole('textbox', { name: /Title/ })
    const field = title.closest('.field')

    // The whole word in the first cell, with the accelerator letter marked
    // inside it rather than beside it.
    expect(field.children[0].textContent).toBe('Title')
    expect(field.children[0].querySelector('u')).toHaveTextContent('T')
    // ...and the input is the next cell, not the fourth.
    expect(field.children[1]).toBe(title)
  })
})

describe('changing an item status', () => {
  const statuses = emptyReference({
    tables: {
      item_status: [
        { code: 'ordered', label: 'Ordered', source: 'seeded' },
        { code: 'received', label: 'Received', source: 'seeded' },
      ],
    },
  })

  function renderReceived() {
    api.getInventoryItem.mockResolvedValue({ ...item, status: 'received' })
    return renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: statuses },
    )
  }

  it('shows the status of an item that has already arrived', async () => {
    renderReceived()
    expect(await screen.findByRole('combobox', { name: 'item_status' })).toHaveValue(
      'received',
    )
  })

  it('sends the new status, so something received in error can be put back', async () => {
    // Receiving only ever moves an item forward. Before this field there was
    // no way to undo a receipt recorded against the wrong row.
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    renderReceived()

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'item_status' }),
      'ordered',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ status: 'ordered' }),
      ),
    )
  })

  it('does not let a new status code be invented from the picker', async () => {
    // Receiving, the outstanding lists and the inventory views all branch on
    // the known status codes; an invented one is a row nothing downstream can
    // reason about. Descriptive vocabularies still grow with use.
    renderReceived()
    const select = await screen.findByRole('combobox', { name: 'item_status' })

    expect(
      within(select).queryByRole('option', { name: /add a new value/i }),
    ).not.toBeInTheDocument()
    // Status is NOT NULL, so there is no blank option to clear it with either.
    expect(within(select).queryByRole('option', { name: '--' })).not.toBeInTheDocument()
  })
})

describe('a banknote in the editor', () => {
  const note = {
    ...item,
    item_kind: 'currency',
    denomination: 'usd_note_1',
    note_type: 'silver_certificate',
    seal_color: 'blue',
    signature_combination: null,
    fed_district: null,
    series_year: 1957,
    series_letter: null,
    serial_number: 'A12345678B',
    derived: { note_type_id: 'note_issue', seal_color_id: 'note_issue' },
  }

  it('shows the note fields, marking what the facts filled in', async () => {
    api.getInventoryItem.mockResolvedValue(note)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)

    expect(await screen.findByDisplayValue('A12345678B')).toBeInTheDocument()
    expect(screen.getByLabelText('note_type')).toHaveValue('silver_certificate')
    const marks = screen.getAllByText('suggested')
    expect(marks).toHaveLength(2)
    expect(marks[0]).toHaveAttribute(
      'title',
      "Filled from the note's denomination and series",
    )
    expect(screen.getByLabelText('note_type')).toHaveAttribute('accesskey', 'a')
  })

  it('drops the mark once the field is changed, and saves the change', async () => {
    api.getInventoryItem.mockResolvedValue(note)
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('A12345678B')

    await user.clear(screen.getByLabelText('seal_color'))
    await user.type(screen.getByLabelText('seal_color'), 'red')
    expect(screen.getAllByText('suggested')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ seal_color: 'red' }),
      ),
    )
  })

  it('keeps the note fields off a coin', async () => {
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: 'coin' })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    expect(screen.queryByLabelText('note_type')).toBeNull()
    expect(screen.queryByText('Serial number')).toBeNull()
  })
})

describe('Attributes', () => {
  const attribute = (code, label, applies_to, attribute_group) => ({
    code,
    label,
    source: 'seeded',
    aliases: [],
    extra: { applies_to, attribute_group },
  })
  const vocabularies = emptyReference({
    tables: {
      item_attribute: [
        attribute('star', 'Star Note', 'currency', 'serial'),
        attribute('no_motto', 'No Motto', 'any', 'variety'),
        attribute('first_strike', 'First Strike', 'coin', 'release'),
        attribute('cac', 'CAC (green sticker)', 'coin', 'verification'),
      ],
    },
  })

  async function open(overrides) {
    api.getInventoryItem.mockResolvedValue({ ...item, version: 3, ...overrides })
    renderWithProviders(<ItemEditForm itemId={12} />, { reference: vocabularies })
    await screen.findByDisplayValue('Mercury Dime')
  }

  const offered = () =>
    within(screen.getByRole('combobox', { name: 'item_attribute' }))
      .getAllByRole('option')
      .map((o) => o.textContent)

  it('lists what the item carries and marks what a rule read', async () => {
    await open({
      item_kind: 'currency',
      attributes: [
        {
          code: 'star',
          label: 'Star Note',
          group: 'serial',
          source: 'derived',
          derived_by: 'serial_pattern',
        },
      ],
    })
    expect(screen.getByText('Star Note')).toBeVisible()
    expect(screen.getByTitle('Read from the serial number')).toHaveTextContent('read')
  })

  it('offers only attributes for this kind of item, and not the ones it has', async () => {
    await open({
      item_kind: 'coin',
      attributes: [
        {
          code: 'cac',
          label: 'CAC (green sticker)',
          group: 'verification',
          source: 'manual',
        },
      ],
    })
    expect(offered()).toEqual(['--', 'No Motto', 'First Strike'])
  })

  it('saves the whole set with the item version', async () => {
    const user = userEvent.setup()
    api.updateInventoryItem.mockResolvedValue({})
    await open({
      item_kind: 'coin',
      attributes: [
        {
          code: 'cac',
          label: 'CAC (green sticker)',
          group: 'verification',
          source: 'manual',
        },
      ],
    })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_attribute' }),
      'first_strike',
    )
    await user.click(screen.getByRole('button', { name: 'Remove CAC (green sticker)' }))
    expect(screen.getByText('First Strike')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        attributes: ['first_strike'],
        version: 3,
      }),
    )
  })

  it('offers nothing until the vocabulary has loaded', async () => {
    api.getInventoryItem.mockResolvedValue({
      ...item,
      item_kind: 'coin',
      attributes: [],
    })
    renderWithProviders(<ItemEditForm itemId={12} />, { reference: emptyReference() })
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.queryByRole('combobox', { name: 'item_attribute' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'item_attribute' })).toBeNull()
  })
})
