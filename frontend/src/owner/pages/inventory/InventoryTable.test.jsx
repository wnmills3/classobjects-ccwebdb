import userEvent from '@testing-library/user-event'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import InventoryTable from './InventoryTable'

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
    ...overrides,
  }
  return { props, ...render(<InventoryTable {...props} />) }
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

  it('selects every row on the page from the header checkbox', async () => {
    const user = userEvent.setup()
    const { props } = setup()
    const header = screen.getAllByRole('columnheader')[0]
    await user.click(within(header).getByRole('checkbox'))
    expect(props.onSelect).toHaveBeenCalledWith([1, 2])
  })
})
