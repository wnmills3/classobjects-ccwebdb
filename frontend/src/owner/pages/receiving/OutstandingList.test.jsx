import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import OutstandingList from './OutstandingList'

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

describe('OutstandingList', () => {
  it('renders only the lines that have not arrived', () => {
    render(<OutstandingList lines={lines} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText('CC-000412')).toBeInTheDocument()
    expect(screen.getByText('CC-000414')).toBeInTheDocument()
    expect(screen.queryByText('CC-000413')).not.toBeInTheDocument()
  })

  it('offers a line written off as missing, since a late arrival can still be received', () => {
    // `missing` means paid for, not cancelled, never arrived -- and things
    // that never arrived sometimes turn up. `received` stays excluded: it
    // has nothing left to do here.
    render(<OutstandingList lines={lines} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText('CC-000415')).toBeInTheDocument()
    expect(screen.queryByText('CC-000413')).not.toBeInTheDocument()
  })

  it('lifts the checked set when a line is toggled', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<OutstandingList lines={lines} selected={[]} onChange={onChange} />)
    const [, firstLineCheckbox] = screen.getAllByRole('checkbox')
    await user.click(firstLineCheckbox)
    expect(onChange).toHaveBeenCalledWith([412])
  })

  it('selects every outstanding line from the header checkbox', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<OutstandingList lines={lines} selected={[]} onChange={onChange} />)
    const [selectAll] = screen.getAllByRole('checkbox')
    await user.click(selectAll)
    expect(onChange).toHaveBeenCalledWith([412, 414, 415])
  })

  it('says so when every line has already arrived', () => {
    render(<OutstandingList lines={[lines[1]]} selected={[]} onChange={vi.fn()} />)
    expect(screen.getByText(/already arrived/i)).toBeInTheDocument()
  })
})
