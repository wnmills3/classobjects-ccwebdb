import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    searchInventory: vi.fn(),
  },
}))

import { api } from '../api'
import { adminAuth, renderWithProviders } from '../../test/helpers'
import { InventoryCoins, InventoryCurrency } from './Inventory'

// Each view searches its own serials: a bar's on Coins, a note's on
// Currency. `matches` is how many items each view's search finds.
function searchFinds(matches) {
  api.searchInventory.mockImplementation((view) =>
    Promise.resolve({
      rows: [],
      total: matches[view] ?? 0,
      facets: {},
      issues: {},
      sortable: [],
    }),
  )
}

function render(page, route) {
  renderWithProviders(page, { auth: adminAuth(), route })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('a serial typed on the view that does not hold it', () => {
  it('points from Coins to the notes in Currency that carry it', async () => {
    searchFinds({ coins: 0, currency: 2 })
    render(<InventoryCoins />, '/inventory/coins?serial_number=4241')

    const hint = await screen.findByText(/2 items in Currency have a serial matching/)
    expect(hint.textContent).toContain('4241')
    const link = screen.getByRole('link', { name: 'Show them' })
    expect(link.getAttribute('href')).toBe('/inventory/currency?serial_number=4241')
    expect(api.searchInventory).toHaveBeenCalledWith('currency', {
      serial_number: '4241',
      limit: 1,
    })
  })

  it('points from Currency to a bar in Coins, in the singular', async () => {
    searchFinds({ coins: 1, currency: 0 })
    render(<InventoryCurrency />, '/inventory/currency?serial_number=AB12')

    await screen.findByText(/1 item in Coins has a serial matching/)
    expect(screen.getByRole('link', { name: 'Show it' }).getAttribute('href')).toBe(
      '/inventory/coins?serial_number=AB12',
    )
  })

  it('says nothing when the other view has none', async () => {
    searchFinds({ coins: 0, currency: 0 })
    render(<InventoryCoins />, '/inventory/coins?serial_number=4241')

    await waitFor(() =>
      expect(api.searchInventory).toHaveBeenCalledWith('currency', {
        serial_number: '4241',
        limit: 1,
      }),
    )
    await screen.findByText('Nothing matches those filters.')
    expect(
      screen.queryByText(/have a serial matching|has a serial matching/),
    ).toBeNull()
  })

  it('asks the other view nothing when no serial is typed', async () => {
    searchFinds({ coins: 5, currency: 5 })
    render(<InventoryCoins />, '/inventory/coins?grade=64')

    await waitFor(() => expect(api.searchInventory).toHaveBeenCalled())
    expect(api.searchInventory.mock.calls.map(([view]) => view)).toEqual(['coins'])
  })
})

describe('the page headings', () => {
  it('names the coin view Coins, as the menu does', async () => {
    searchFinds({ coins: 0 })
    render(<InventoryCoins />, '/inventory/coins')
    expect(await screen.findByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
  })
})
