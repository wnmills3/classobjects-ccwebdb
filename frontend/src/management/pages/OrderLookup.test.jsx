import userEvent from '@testing-library/user-event'
import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listPurchaseOrders: vi.fn(),
  },
}))

import { api } from '../api'
import { renderWithProviders } from '../../test/helpers'
import OrderLookup from './OrderLookup'

const purchase = (id, fields) => ({
  id,
  order_number: null,
  vendor: 'ebay.com',
  seller: null,
  ordered_on: null,
  outstanding: 0,
  total: 1,
  total_cost: '10.00',
  ...fields,
})

//: Sent in no useful order -- not by date, vendor, cost or number -- so no
//: sort passes by leaving them as they came.
const ORDERS = [
  purchase(3, {
    order_number: '27-11261-24607',
    seller: 'drh9989',
    ordered_on: '2026-03-10',
    total: 2,
    total_cost: '55.25',
  }),
  purchase(7, {
    order_number: 'Invoice 51084',
    vendor: 'A.J.C.A.',
    ordered_on: '2026-09-22',
    outstanding: 4,
    total: 12,
    total_cost: '1250.00',
  }),
  purchase(1, { vendor: 'Coin show', total_cost: '9.00' }),
  purchase(5, {
    order_number: '08-15205-85245',
    seller: 'GoldStandard',
    ordered_on: '2025-12-24',
    outstanding: 1,
    total_cost: '120.00',
  }),
]

async function open(orders = ORDERS) {
  api.listPurchaseOrders.mockResolvedValue(orders)
  renderWithProviders(<OrderLookup />)
  await screen.findByRole('status')
  return userEvent.setup()
}

/** The purchase numbers shown, top to bottom. */
function found() {
  const table = screen.queryByRole('table', { name: 'Purchases found' })
  if (!table) return []
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').at(-1).textContent)
}

const text = () => screen.getByLabelText('Order number, vendor or seller')
const header = (name) => screen.getByRole('button', { name: new RegExp(`^${name}`) })

beforeEach(() => vi.resetAllMocks())

describe('OrderLookup', () => {
  it('lists every purchase, newest first, an undated one last', async () => {
    await open()
    expect(found()).toEqual(['#7', '#3', '#5', '#1'])
    expect(screen.getByRole('status')).toHaveTextContent(
      '4 of 4 purchases, costing $1,434.25',
    )
  })

  it('shows what each purchase is: number, date, vendor, seller, items, cost', async () => {
    await open()
    const row = screen.getByRole('row', { name: /27-11261-24607/ })
    expect(
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    ).toEqual([
      '27-11261-24607',
      'Mar 10, 2026',
      'ebay.com',
      'drh9989',
      '2',
      // Nothing still to arrive is left empty, not shown as a nought.
      '',
      '$55.25',
      '#3',
    ])
  })

  it.each([
    // Part of an order number, from its middle.
    ['15205', ['#5']],
    // Part of a vendor's name, capitals aside.
    ['EBAY', ['#3', '#5']],
    ['a.j', ['#7']],
    // A seller's name.
    ['goldstand', ['#5']],
    // A purchase number, exactly: #1 is not also #7's "51084".
    ['#1', ['#1']],
    // Bare digits are a purchase number and part of an order number alike:
    // purchase 7, and the order numbered 27-11261-24607.
    ['7', ['#7', '#3']],
    ['nothing like this', []],
  ])('narrows to what "%s" matches', async (typed, numbers) => {
    const user = await open()
    await user.type(text(), typed)
    expect(found()).toEqual(numbers)
  })

  it('narrows to the order dates given, either end included', async () => {
    await open()
    fireEvent.change(screen.getByLabelText('Ordered from'), {
      target: { value: '2026-03-10' },
    })
    // From that day on, itself included; the undated one is in no range.
    expect(found()).toEqual(['#7', '#3'])

    fireEvent.change(screen.getByLabelText('Ordered to'), {
      target: { value: '2026-03-10' },
    })
    expect(found()).toEqual(['#3'])

    fireEvent.change(screen.getByLabelText('Ordered from'), { target: { value: '' } })
    expect(found()).toEqual(['#3', '#5'])
  })

  it('narrows to the purchases with something still to arrive', async () => {
    const user = await open()
    await user.click(screen.getByRole('checkbox', { name: /items not yet received/i }))
    expect(found()).toEqual(['#7', '#5'])
  })

  it('applies every box at once, and says how many are left and what they cost', async () => {
    const user = await open()
    await user.type(text(), 'ebay')
    await user.click(screen.getByRole('checkbox', { name: /items not yet received/i }))

    expect(found()).toEqual(['#5'])
    expect(screen.getByRole('status')).toHaveTextContent(
      '1 of 4 purchases, costing $120.00',
    )
  })

  it('says so when nothing matches, and Clear brings everything back', async () => {
    const user = await open()
    const clear = screen.getByRole('button', { name: 'Clear' })
    expect(clear).toBeDisabled()

    await user.type(text(), 'zzz')
    expect(
      screen.getByText('No purchase matches. Clear a box to widen the search.'),
    ).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('0 of 4 purchases')

    await user.click(clear)
    expect(text()).toHaveValue('')
    expect(found()).toHaveLength(4)
  })

  it('sorts by a column from its heading, and back the other way', async () => {
    const user = await open()
    // An amount, largest first: 1,250 before 120 before 55.25 before 9.
    await user.click(header('Cost'))
    expect(found()).toEqual(['#7', '#5', '#3', '#1'])
    await user.click(header('Cost'))
    expect(found()).toEqual(['#1', '#3', '#5', '#7'])

    await user.click(header('Vendor'))
    expect(found()).toEqual(['#7', '#1', '#3', '#5'])

    // A count: the most still to arrive first, and none is a count too.
    await user.click(header('Not received'))
    expect(found().slice(0, 2)).toEqual(['#7', '#5'])

    // A purchase with no seller goes last whichever way it runs.
    await user.click(header('Seller'))
    expect(found().slice(0, 2)).toEqual(['#3', '#5'])
    await user.click(header('Seller'))
    expect(found().slice(0, 2)).toEqual(['#5', '#3'])
  })

  it('opens a purchase from its order number, on the Purchases page', async () => {
    await open()
    expect(screen.getByRole('link', { name: '08-15205-85245' })).toHaveAttribute(
      'href',
      '/purchases?order=5',
    )
    // One with no number is still a link to itself.
    expect(screen.getByRole('link', { name: 'no order number' })).toHaveAttribute(
      'href',
      '/purchases?order=1',
    )
  })

  it('says when no purchase has been recorded', async () => {
    await open([])
    expect(screen.getByText('No purchases recorded yet.')).toBeVisible()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('says so when the purchases cannot be loaded', async () => {
    api.listPurchaseOrders.mockRejectedValue(new Error('Not signed in'))
    renderWithProviders(<OrderLookup />)
    expect(await screen.findByText('Not signed in')).toBeVisible()
    expect(screen.queryByLabelText('Ordered from')).toBeNull()
  })
})
