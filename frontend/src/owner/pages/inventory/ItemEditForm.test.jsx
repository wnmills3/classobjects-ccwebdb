import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
