import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import OrderLines from './OrderLines'

const lines = [
  {
    id: 412,
    item_code: 'CC-000412',
    description: '1881-S Morgan $1',
    item_cost: '84.00',
    status: 'ordered',
  },
  {
    id: 413,
    item_code: 'CC-000413',
    description: '1923 Peace $1',
    item_cost: '91.00',
    status: 'received',
  },
  {
    id: 414,
    item_code: 'CC-000414',
    description: '1921 Morgan $1',
    item_cost: '60.00',
    status: 'ordered',
  },
  {
    id: 415,
    item_code: 'CC-000415',
    description: '1899-O Morgan $1',
    item_cost: '75.00',
    status: 'missing',
  },
]

describe('OrderLines', () => {
  it('shows every line on the order, arrived ones included', () => {
    render(<OrderLines lines={lines} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText('CC-000412')).toBeInTheDocument()
    expect(screen.getByText('CC-000414')).toBeInTheDocument()
    // Unlike the old OutstandingList, an already-arrived line is shown too --
    // there is otherwise no way to see what an order contained once
    // everything on it has arrived.
    expect(screen.getByText('CC-000413')).toBeInTheDocument()
  })

  it('offers a line written off as missing, since a late arrival can still be received', () => {
    // `missing` means paid for, not cancelled, never arrived -- and things
    // that never arrived sometimes turn up.
    render(<OrderLines lines={lines} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText('CC-000415')).toBeInTheDocument()
  })

  it('sorts not-yet-arrived lines before the rest, missing above received', () => {
    render(<OrderLines lines={lines} selected={[]} onChange={vi.fn()} />)
    const codes = screen
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.cells[1].textContent)
    // ordered, ordered, missing (not-yet-arrived, item-code order), then received.
    expect(codes).toEqual(['CC-000412', 'CC-000414', 'CC-000415', 'CC-000413'])
  })

  it('shows each line status so the two groups read apart', () => {
    render(<OrderLines lines={lines} selected={[]} onChange={vi.fn()} />)
    const rows = screen.getAllByRole('row').slice(1)
    const statusCells = rows.map((row) => row.cells[row.cells.length - 1].textContent)
    expect(statusCells).toEqual(['ordered', 'ordered', 'missing', 'received'])
  })

  it('lifts the checked set when a not-yet-arrived line is toggled', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<OrderLines lines={lines} selected={[]} onChange={onChange} />)
    const [, firstLineCheckbox] = screen.getAllByRole('checkbox')
    await user.click(firstLineCheckbox)
    expect(onChange).toHaveBeenCalledWith([412])
  })

  it('does not let a received line be ticked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<OrderLines lines={lines} selected={[]} onChange={onChange} />)
    const row = screen.getByText('CC-000413').closest('tr')
    const checkbox = row.querySelector('input[type="checkbox"]')
    expect(checkbox).toBeDisabled()
    expect(checkbox).not.toBeChecked()
    await user.click(checkbox)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('selects every not-yet-arrived line from the header checkbox, skipping received', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<OrderLines lines={lines} selected={[]} onChange={onChange} />)
    const [selectAll] = screen.getAllByRole('checkbox')
    await user.click(selectAll)
    expect(onChange).toHaveBeenCalledWith([412, 414, 415])
  })

  it('says so when an order has no lines at all', () => {
    render(<OrderLines lines={[]} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText(/no lines/i)).toBeInTheDocument()
  })
})
