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
]

describe('OrderPicker', () => {
  it('shows only orders with something outstanding', () => {
    render(<OrderPicker orders={orders} selectedId={null} onPick={vi.fn()} />)
    expect(screen.getByText(/27-1234/)).toBeInTheDocument()
    expect(screen.queryByText(/27-1235/)).not.toBeInTheDocument()
  })

  it('calls onPick with the order id when clicked', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<OrderPicker orders={orders} selectedId={null} onPick={onPick} />)
    await user.click(screen.getByText(/27-1234/))
    expect(onPick).toHaveBeenCalledWith(1)
  })

  it('says so when nothing is outstanding', () => {
    render(<OrderPicker orders={[orders[1]]} selectedId={null} onPick={vi.fn()} />)
    expect(screen.getByText(/nothing outstanding/i)).toBeInTheDocument()
  })
})
