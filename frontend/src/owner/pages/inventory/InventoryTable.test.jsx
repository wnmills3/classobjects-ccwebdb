import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import InventoryTable from './InventoryTable'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'
import { date } from '../../../shared/format'
import { renderWithProviders } from '../../../test/helpers'

const config = {
  columns: [
    ['Code', 'item_code'],
    ['Description', 'description'],
    ['Cost', 'item_cost', 'money'],
  ],
}

const rows = [
  { id: 1, item_code: 'C-001', description: 'Morgan Dollar', item_cost: '1234.5' },
  { id: 2, item_code: 'C-002', description: null, item_cost: null },
]

function setup(overrides = {}) {
  const props = {
    config,
    rows,
    current: {},
    apply: vi.fn(),
    selected: [],
    onSelect: vi.fn(),
    onOpen: vi.fn(),
    sortable: ['item_code'],
    ...overrides,
  }
  return { props, ...renderWithProviders(<InventoryTable {...props} />) }
}

describe('InventoryTable', () => {
  it('renders a header per configured column and a row per result', () => {
    setup()
    expect(screen.getByRole('columnheader', { name: /code/i })).toBeInTheDocument()
    expect(screen.getByText('Morgan Dollar')).toBeInTheDocument()
    expect(screen.getByText('C-002')).toBeInTheDocument()
  })

  it('formats a money column and shows a dash for an absent value', () => {
    setup()
    expect(screen.getByText('$1,234.50')).toBeInTheDocument()
    // The empty cells render a dash rather than "$0.00" or a blank, because
    // the table guards null before it reaches money().
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
  })

  it('sorts ascending on the first click of an unsorted column', async () => {
    const user = userEvent.setup()
    const { props } = setup()
    await user.click(screen.getByRole('columnheader', { name: /code/i }))
    // Ascending is the empty string, not 'false' -- the value goes straight
    // into the query string.
    expect(props.apply).toHaveBeenCalledWith({ sort: 'item_code', desc: '' })
  })

  it('flips to descending when the column is already the ascending sort', async () => {
    const user = userEvent.setup()
    const { props } = setup({ current: { sort: 'item_code', desc: '' } })
    await user.click(screen.getByRole('columnheader', { name: /code/i }))
    expect(props.apply).toHaveBeenCalledWith({ sort: 'item_code', desc: 'true' })
  })

  it('marks the sorted column and flips direction', () => {
    setup({ current: { sort: 'item_code', desc: 'true' } })
    // The header carries a direction marker only for the active sort column.
    expect(screen.getByRole('columnheader', { name: /code v/i })).toBeInTheDocument()
  })

  it('does not offer a sort the server cannot perform', async () => {
    // The server lists what it can sort by; a header outside that list used
    // to look clickable and put "cannot sort by" on the page.
    const user = userEvent.setup()
    const { props } = setup({ sortable: ['item_code'] })
    await user.click(screen.getByRole('columnheader', { name: /cost/i }))
    expect(props.apply).not.toHaveBeenCalled()
  })

  it('selects every row on the page from the header checkbox', async () => {
    const user = userEvent.setup()
    const { props } = setup()
    const header = screen.getAllByRole('columnheader')[0]
    await user.click(within(header).getByRole('checkbox'))
    expect(props.onSelect).toHaveBeenCalledWith([1, 2])
  })
})

describe('two lines per item', () => {
  const twoLine = {
    columns: [
      ['Code', 'item_code'],
      ['Denomination', 'denomination_label'],
    ],
    detail: 'description',
  }

  it('puts the description on a second line spanning the columns', () => {
    setup({ config: twoLine })
    const detail = screen.getByText('Morgan Dollar').closest('td')
    expect(detail).toHaveAttribute('colspan', '2')
    expect(
      screen.queryByRole('columnheader', { name: /description/i }),
    ).not.toBeInTheDocument()
  })

  it('adds no second line for an item without a description', () => {
    setup({ config: twoLine })
    // The header row, two item rows, and one detail row: C-002 has none.
    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  it('is how both inventories are shown, with denomination on the first line', () => {
    for (const view of [COIN_VIEW, CURRENCY_VIEW]) {
      expect(view.detail, view.view).toBe('description')
      const keys = view.columns.map(([, key]) => key)
      expect(keys, view.view).toContain('denomination_label')
      expect(keys, view.view).not.toContain('description')
    }
  })

  it('shows a note grade by its label, not its code', () => {
    // A note's grade code is `N64`; "Choice Uncirculated 64" is what is read.
    const keys = CURRENCY_VIEW.columns.map(([, key]) => key)
    expect(keys).toContain('grade_label')
    expect(keys).not.toContain('grade')
  })
})

describe('the order column', () => {
  const orderConfig = {
    columns: [
      ['Code', 'item_code'],
      ['Order', 'order_number', 'order'],
    ],
  }

  it('links a numbered order to Receiving, named by its number', () => {
    setup({
      config: orderConfig,
      rows: [
        { id: 1, item_code: 'C-001', purchase_order_id: 7, order_number: '123-456' },
      ],
    })
    const link = screen.getByRole('link', { name: '123-456' })
    expect(link.getAttribute('href')).toMatch(/\/receiving\?order=7$/)
  })

  it('links an unnumbered order by its vendor and date', () => {
    setup({
      config: orderConfig,
      rows: [
        {
          id: 2,
          item_code: 'C-002',
          purchase_order_id: 8,
          order_number: null,
          vendor: 'ebay.com',
          ordered_on: '2025-01-09',
        },
      ],
    })
    const link = screen.getByRole('link')
    expect(link).toHaveTextContent('ebay.com')
    expect(link).toHaveTextContent(date('2025-01-09'))
    expect(link.getAttribute('href')).toMatch(/\/receiving\?order=8$/)
  })

  it('shows a dash and no link when the item has no purchase order', () => {
    setup({
      config: orderConfig,
      rows: [{ id: 3, item_code: 'C-003', purchase_order_id: null }],
    })
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText('-')).toBeInTheDocument()
  })
})
