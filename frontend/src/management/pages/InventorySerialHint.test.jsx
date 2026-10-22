import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, Route, Routes, useNavigate } from 'react-router-dom'
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

// Both pages behind routes, as the console mounts them, so a page that sends
// the reader to the other one can be followed there. `/start` is a page to
// come from, and the Back button proves what history was left behind.
function BackButton() {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>Back</button>
}

function renderBothPages(route) {
  renderWithProviders(
    <>
      <BackButton />
      <Routes>
        <Route
          path="/start"
          element={<Link to="/inventory/coins?serial_number=4241">Search coins</Link>}
        />
        <Route path="/inventory/coins" element={<InventoryCoins />} />
        <Route path="/inventory/currency" element={<InventoryCurrency />} />
      </Routes>
    </>,
    { auth: adminAuth(), route },
  )
}

describe('a serial found only on the other view', () => {
  it('opens Currency with the serial when no coin has it, and says why', async () => {
    searchFinds({ coins: 0, currency: 3 })
    renderBothPages('/inventory/coins?serial_number=4241')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Currency' }),
    ).toBeTruthy()
    const notice = await screen.findByText(/No items in Coins have a serial matching/)
    expect(notice.textContent).toContain('“4241”')
    expect(notice.textContent).toContain('the 3 in Currency')
    expect(api.searchInventory).toHaveBeenCalledWith(
      'currency',
      expect.objectContaining({ serial_number: '4241', facets: true }),
    )
  })

  it('says it in the singular for one item', async () => {
    searchFinds({ coins: 1, currency: 0 })
    renderBothPages('/inventory/currency?serial_number=AB12')

    expect(await screen.findByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
    const notice = await screen.findByText(
      /No items in Currency have a serial matching/,
    )
    expect(notice.textContent).toContain('the 1 in Coins')
  })

  it('leaves no empty Coins page behind for Back to return to', async () => {
    searchFinds({ coins: 0, currency: 3 })
    renderBothPages('/start')

    await userEvent.click(screen.getByRole('link', { name: 'Search coins' }))
    await screen.findByRole('heading', { level: 1, name: 'Currency' })
    await userEvent.click(screen.getByRole('button', { name: 'Back' }))

    // Back from the switch reaches the page the search started from. Were
    // the empty Coins page still in history, Back would land on it and it
    // would send the reader on to Currency again.
    expect(await screen.findByRole('link', { name: 'Search coins' })).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
  })

  it('stays on Coins when another filter is set too, and only points', async () => {
    searchFinds({ coins: 0, currency: 3 })
    renderBothPages('/inventory/coins?serial_number=4241&grade=64')

    await screen.findByText(/3 items in Currency have a serial matching/)
    expect(screen.getByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
    expect(screen.queryByText(/No items in Coins/)).toBeNull()
  })

  it('stays on Coins when neither view has the serial', async () => {
    searchFinds({ coins: 0, currency: 0 })
    renderBothPages('/inventory/coins?serial_number=4241')

    await screen.findByText('Nothing matches those filters.')
    await waitFor(() =>
      expect(api.searchInventory).toHaveBeenCalledWith('currency', {
        serial_number: '4241',
        limit: 1,
      }),
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
  })
})

describe('a serial typed on the view that does not hold it', () => {
  it('points from Coins to the notes in Currency that carry it too', async () => {
    searchFinds({ coins: 1, currency: 2 })
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
    searchFinds({ coins: 1, currency: 1 })
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
