import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import OrderPicker from './OrderPicker'

const orders = [
  {
    id: 1,
    order_number: '27-1234',
    vendor: 'eBay',
    ordered_on: '2026-08-30',
    outstanding: 3,
    total: 5,
  },
  {
    id: 2,
    order_number: '27-1235',
    vendor: 'Heritage',
    ordered_on: '2026-08-31',
    outstanding: 0,
    total: 4,
  },
  {
    id: 3,
    order_number: '27-1236',
    vendor: 'GreatCollections',
    ordered_on: '2026-09-01',
    // The backend counts `missing` as outstanding too; this order's only
    // receivable line is `missing`, so it must not be treated as fully done.
    outstanding: 1,
    total: 1,
  },
]

describe('OrderPicker', () => {
  it('shows every order, including one that has fully arrived', () => {
    // A fully-received order is still worth looking at -- there is no other
    // way to see what it contained once everything on it arrived.
    render(<OrderPicker orders={orders} selectedId={null} onPick={vi.fn()} />)
    expect(screen.getByText(/27-1234/)).toBeInTheDocument()
    expect(screen.getByText(/27-1235/)).toBeInTheDocument()
  })

  it('offers an order whose only receivable line is missing', () => {
    render(<OrderPicker orders={orders} selectedId={null} onPick={vi.fn()} />)
    expect(screen.getByText(/27-1236/)).toBeInTheDocument()
  })

  it('calls onPick with the order id when clicked', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderPicker orders={orders} selectedId={null} onPick={onPick} />)
    await user.click(screen.getByText(/27-1234/))
    expect(onPick).toHaveBeenCalledWith(1)
  })

  it('says so when there are no purchase orders at all', () => {
    render(<OrderPicker orders={[]} selectedId={null} onPick={vi.fn()} />)
    expect(screen.getByText(/no purchase orders/i)).toBeInTheDocument()
  })
})
