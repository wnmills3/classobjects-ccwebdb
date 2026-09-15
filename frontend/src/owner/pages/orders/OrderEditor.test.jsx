import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listCustomers: vi.fn(),
    listUsers: vi.fn(),
    listCatalog: vi.fn(),
    getCatalogItem: vi.fn(),
    createOrderFor: vi.fn(),
    reviseOrder: vi.fn(),
    customerForUser: vi.fn(),
  },
}))

import { api } from '../../api'
import OrderEditor from './OrderEditor'

const ORDER = {
  id: 12,
  version: 3,
  customer_id: 5,
  customer_name: 'Ada Lovelace',
  status: 'paid',
  notes: null,
  total_amount: '378.00',
  items: [{ id: 1, listing_id: 3, title: 'Morgan', quantity: 2, unit_price: '189.00' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listCustomers.mockResolvedValue([
    { id: 5, user_id: 2, display_name: 'Ada Lovelace', email: 'ada@example.com' },
    { id: 6, user_id: null, display_name: 'Walk-in', email: null },
  ])
  api.listUsers.mockResolvedValue([
    { id: 2, email: 'ada@example.com', full_name: 'Ada Lovelace' },
    { id: 9, email: 'new@example.com', full_name: 'New Person' },
  ])
  api.getCatalogItem.mockResolvedValue({
    id: 3,
    price: '189.00',
    quantity_available: 4,
  })
  api.listCatalog.mockResolvedValue({
    items: [{ id: 4, title: 'Dime', price: '10.00', quantity_available: 7 }],
  })
  api.createOrderFor.mockResolvedValue({ id: 13 })
  api.reviseOrder.mockResolvedValue({ id: 12 })
})

const setup = async (order = null) => {
  const user = userEvent.setup()
  const onSaved = vi.fn()
  render(<OrderEditor order={order} onSaved={onSaved} onClose={vi.fn()} />)
  await screen.findByRole('combobox', { name: 'Customer' })
  return { user, onSaved }
}

describe('OrderEditor', () => {
  it('prefills an order being edited and shows the paid warning', async () => {
    await setup(ORDER)
    expect(screen.getByRole('combobox', { name: 'Customer' })).toHaveValue('c:5')
    expect(screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })).toHaveValue(
      2,
    )
    expect(screen.getByRole('textbox', { name: 'Price of Morgan' })).toHaveValue(
      '189.00',
    )
    expect(screen.getByText(/this order is paid/i)).toBeInTheDocument()
    expect(screen.getByText('Total $378.00')).toBeInTheDocument()
  })

  it('offers accounts without a customer record', async () => {
    await setup()
    const select = screen.getByRole('combobox', { name: 'Customer' })
    const labels = Array.from(select.querySelectorAll('option')).map(
      (o) => o.textContent,
    )
    expect(labels).toContain('New Person (new@example.com), account, no orders yet')
    expect(labels.filter((l) => l.includes('ada@example.com'))).toHaveLength(1)
  })

  it('re-quantities, re-prices and saves a revision with the loaded version', async () => {
    const { user, onSaved } = await setup(ORDER)
    const qty = screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })
    await user.clear(qty)
    await user.type(qty, '3')
    const price = screen.getByRole('textbox', { name: 'Price of Morgan' })
    await user.clear(price)
    await user.type(price, '150')
    expect(screen.getByText('Total $450.00')).toBeInTheDocument()
    expect(screen.getByText('listing price $189.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(api.reviseOrder).toHaveBeenCalledWith(12, {
      version: 3,
      customer_id: 5,
      notes: null,
      items: [{ listing_id: 3, quantity: 3, unit_price: '150.00' }],
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('adds an item from a catalogue search and removes another', async () => {
    const { user } = await setup(ORDER)
    await user.type(screen.getByRole('searchbox', { name: 'Find item' }), 'dime')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await user.click(await screen.findByRole('button', { name: 'Add Dime' }))
    await user.click(screen.getByRole('button', { name: 'Remove Morgan' }))
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(api.listCatalog).toHaveBeenCalledWith({
      q: 'dime',
      in_stock: true,
      limit: 10,
    })
    expect(api.reviseOrder).toHaveBeenCalledWith(
      12,
      expect.objectContaining({
        items: [{ listing_id: 4, quantity: 1, unit_price: '10.00' }],
      }),
    )
  })

  it('creates a customer record for an account before placing the order', async () => {
    api.customerForUser.mockResolvedValue({ id: 77 })
    const { user } = await setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Customer' }), 'u:9')
    await user.type(screen.getByRole('searchbox', { name: 'Find item' }), 'dime')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await user.click(await screen.findByRole('button', { name: 'Add Dime' }))
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    await waitFor(() => expect(api.customerForUser).toHaveBeenCalledWith(9))
    expect(api.createOrderFor).toHaveBeenCalledWith(77, {
      notes: null,
      items: [{ listing_id: 4, quantity: 1, unit_price: '10.00' }],
    })
  })

  it('keeps the edits when the server refuses', async () => {
    api.reviseOrder.mockRejectedValue(
      new Error('Only 1 more of listing 3 are available'),
    )
    const { user, onSaved } = await setup(ORDER)
    const qty = screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })
    await user.clear(qty)
    await user.type(qty, '9')
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(await screen.findByText(/only 1 more of listing 3/i)).toBeInTheDocument()
    expect(qty).toHaveValue(9)
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('refuses to save an empty order or one with no customer', async () => {
    const { user } = await setup()
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    expect(screen.getByText('Choose a customer.')).toBeInTheDocument()
    expect(api.createOrderFor).not.toHaveBeenCalled()
  })
})
