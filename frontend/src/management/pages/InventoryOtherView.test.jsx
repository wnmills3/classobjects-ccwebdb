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

// `matches` is how many items each view's search finds. A view named in
// `refuses` answers the other view's count as the API answers a filter it
// does not know -- with an error -- while its own page search still works:
// only the count asks for one row.
function searchFinds(matches, { refuses = [] } = {}) {
  api.searchInventory.mockImplementation((view, params) => {
    if (refuses.includes(view) && params.limit === 1) {
      return Promise.reject(new Error('Unknown filter'))
    }
    return Promise.resolve({
      rows: [],
      total: matches[view] ?? 0,
      facets: {},
      issues: {},
      sortable: [],
    })
  })
}

function countsAsked(view) {
  return api.searchInventory.mock.calls
    .filter(([v, params]) => v === view && params.limit === 1)
    .map(([, params]) => params)
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

describe('a search that finds nothing here but something on the other view', () => {
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

  it('opens Coins for a grade no note has, carrying the grade', async () => {
    searchFinds({ coins: 50, currency: 0 })
    renderBothPages('/inventory/currency?grade=70&sort=year&desc=true')

    expect(await screen.findByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
    const notice = await screen.findByText(/No items in Currency match these filters/)
    expect(notice.textContent).toContain('the 50 in Coins')
    // Counted and opened with the filters, not the sort: a column sorted on
    // one view need not exist on the other.
    expect(countsAsked('coins')).toEqual([{ grade: '70', limit: 1 }])
    expect(api.searchInventory).toHaveBeenCalledWith('coins', {
      grade: '70',
      facets: true,
      limit: expect.any(Number),
    })
  })

  it('carries every filter, a serial and a grade alike', async () => {
    searchFinds({ coins: 0, currency: 3 })
    renderBothPages('/inventory/coins?serial_number=4241&grade=64')

    await screen.findByRole('heading', { level: 1, name: 'Currency' })
    await screen.findByText(/No items in Coins match these filters/)
    expect(api.searchInventory).toHaveBeenCalledWith(
      'currency',
      expect.objectContaining({ serial_number: '4241', grade: '64', facets: true }),
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

  it('stays when the other view does not know one of the filters', async () => {
    searchFinds({ coins: 0, currency: 3 }, { refuses: ['currency'] })
    renderBothPages('/inventory/coins?mint_mark=S')

    await waitFor(() => expect(countsAsked('currency')).toHaveLength(1))
    await screen.findByText('Nothing matches those filters.')
    expect(screen.getByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
  })

  it('stays when neither view has a match', async () => {
    searchFinds({ coins: 0, currency: 0 })
    renderBothPages('/inventory/coins?serial_number=4241')

    await screen.findByText('Nothing matches those filters.')
    await waitFor(() =>
      expect(countsAsked('currency')).toEqual([{ serial_number: '4241', limit: 1 }]),
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
  })

  it('asks the other view nothing when nothing is searched for', async () => {
    searchFinds({ coins: 0, currency: 5 })
    render(<InventoryCoins />, '/inventory/coins?sort=year')

    await screen.findByText('Nothing matches those filters.')
    expect(countsAsked('currency')).toEqual([])
  })
})

describe('a serial that matches here and on the other view', () => {
  it('points from Coins to the notes in Currency that carry it too', async () => {
    searchFinds({ coins: 1, currency: 2 })
    render(<InventoryCoins />, '/inventory/coins?serial_number=4241')

    const hint = await screen.findByText(/2 items in Currency have a serial matching/)
    expect(hint.textContent).toContain('4241')
    const link = screen.getByRole('link', { name: 'Show them' })
    expect(link.getAttribute('href')).toBe('/inventory/currency?serial_number=4241')
    expect(countsAsked('currency')).toEqual([{ serial_number: '4241', limit: 1 }])
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
    searchFinds({ coins: 1, currency: 0 })
    render(<InventoryCoins />, '/inventory/coins?serial_number=4241')

    await waitFor(() => expect(countsAsked('currency')).toHaveLength(1))
    expect(
      screen.queryByText(/have a serial matching|has a serial matching/),
    ).toBeNull()
  })

  it('asks the other view nothing for other filters with matches here', async () => {
    // A year or a grade matches on both views; pointing at the other one
    // after every such search would be noise, and a second query each time.
    searchFinds({ coins: 5, currency: 5 })
    render(<InventoryCoins />, '/inventory/coins?grade=64')

    await waitFor(() => expect(api.searchInventory).toHaveBeenCalled())
    await screen.findByRole('button', { name: 'Review these 0' })
    expect(countsAsked('currency')).toEqual([])
  })
})

describe('the page headings', () => {
  it('names the coin view Coins, as the menu does', async () => {
    searchFinds({ coins: 0 })
    render(<InventoryCoins />, '/inventory/coins')
    expect(await screen.findByRole('heading', { level: 1, name: 'Coins' })).toBeTruthy()
  })
})
