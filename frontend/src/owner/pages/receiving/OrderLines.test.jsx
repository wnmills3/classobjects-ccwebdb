import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import OrderLines from './OrderLines'

const lines = [
  {
    id: 412,
    item_code: 'CC-000412',
    source_title: '1881-S Morgan $1',
    description: '1881-S Morgan $1',
    item_cost: '84.00',
    status: 'ordered',
  },
  {
    id: 413,
    item_code: 'CC-000413',
    source_title: '1923 Peace $1',
    description: '1923 Peace $1',
    item_cost: '91.00',
    status: 'received',
  },
  {
    id: 414,
    item_code: 'CC-000414',
    source_title: '1921 Morgan $1',
    description: '1921 Morgan $1',
    item_cost: '60.00',
    status: 'ordered',
  },
  {
    id: 415,
    item_code: 'CC-000415',
    source_title: '1899-O Morgan $1',
    description: '1899-O Morgan $1',
    item_cost: '75.00',
    status: 'missing',
  },
]

describe('OrderLines', () => {
  it('shows every line on the order, arrived ones included', () => {
    render(<OrderLines lines={lines} onPick={vi.fn()} />)
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
    render(<OrderLines lines={lines} onPick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'CC-000415' })).toBeInTheDocument()
  })

  it('sorts not-yet-arrived lines before the rest, missing above received', () => {
    render(<OrderLines lines={lines} onPick={vi.fn()} />)
    const codes = screen
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.cells[0].textContent)
    // ordered, ordered, missing (not-yet-arrived, item-code order), then received.
    expect(codes).toEqual(['CC-000412', 'CC-000414', 'CC-000415', 'CC-000413'])
  })

  it('shows each line status so the two groups read apart', () => {
    render(<OrderLines lines={lines} onPick={vi.fn()} />)
    const rows = screen.getAllByRole('row').slice(1)
    const statusCells = rows.map((row) => row.cells[3].textContent)
    expect(statusCells).toEqual(['ordered', 'ordered', 'missing', 'received'])
  })

  it('hands the whole picked line up, not just its id', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderLines lines={lines} onPick={onPick} />)

    await user.click(screen.getByRole('button', { name: 'CC-000412' }))

    // The dialog names what it is about, so it needs the code and title too.
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 412 }))
  })

  it('opens a line exactly once when its own button is clicked', async () => {
    // The row carries a click handler for the mouse and the item code is a
    // button for the keyboard; without stopPropagation the button's click
    // would bubble to the row and open the dialog twice.
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderLines lines={lines} onPick={onPick} />)

    await user.click(screen.getByRole('button', { name: 'CC-000414' }))

    expect(onPick).toHaveBeenCalledTimes(1)
  })

  it('opens the line when the row itself is clicked', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderLines lines={lines} onPick={onPick} />)

    await user.click(screen.getByText('1921 Morgan $1'))

    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 414 }))
  })

  it('does not open a line that has already arrived', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderLines lines={lines} onPick={onPick} />)

    // Inert: no button to tab to, and clicking the row does nothing either.
    expect(screen.queryByRole('button', { name: 'CC-000413' })).not.toBeInTheDocument()
    await user.click(screen.getByText('CC-000413'))

    expect(onPick).not.toHaveBeenCalled()
  })

  it("shows the item's own title, falling back to the description", () => {
    // `source_title` is what an entry form's Title box wrote; older imported
    // lines have only a description. The fixture above sets both to the same
    // string, so this uses its own rows to tell the two apart.
    render(
      <OrderLines
        lines={[
          { ...lines[0], source_title: '1881-S Morgan Dollar', description: 'Lot 44' },
          { ...lines[2], source_title: '', description: 'Auction #652' },
        ]}
        onPick={vi.fn()}
      />,
    )
    expect(screen.getByText('1881-S Morgan Dollar')).toBeInTheDocument()
    expect(screen.queryByText('Lot 44')).not.toBeInTheDocument()
    expect(screen.getByText('Auction #652')).toBeInTheDocument()
  })

  it('says so when an order has no lines at all', () => {
    render(<OrderLines lines={[]} onPick={vi.fn()} />)
    expect(screen.getByText(/no lines/i)).toBeInTheDocument()
  })
})
