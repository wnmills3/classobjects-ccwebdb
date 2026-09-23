import userEvent from '@testing-library/user-event'
import { act, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({ api: { searchInventory: vi.fn() } }))

import { api } from '../../api'
import ItemFinder from './ItemFinder'
import { emptyReference, renderWithProviders } from '../../../test/helpers'

//: The two vocabularies the finder picks from, as the reference context
//: would hold them once loaded.
const vocabularies = emptyReference({
  tables: {
    denomination: [
      { code: 'usd_coin_0_01', label: 'Cent', source: 'seeded' },
      { code: 'usd_coin_1_00', label: 'Dollar', source: 'seeded' },
    ],
    item_status: [
      { code: 'ordered', label: 'Ordered', source: 'seeded' },
      { code: 'received', label: 'Received', source: 'seeded' },
    ],
  },
})

const MORGAN = {
  id: 412,
  item_code: 'CC-000412',
  description: '1881-S Morgan $1',
  order_number: '114-4452-X',
  vendor: 'ebay.com',
}

beforeEach(() => {
  vi.clearAllMocks()
  // View- and status-aware: a mock answering every request the same way
  // would hide which views and statuses were actually asked for.
  api.searchInventory.mockImplementation((view, params) =>
    Promise.resolve(
      view === 'coins' && params.status === 'ordered'
        ? { rows: [MORGAN], total: 1 }
        : { rows: [], total: 0 },
    ),
  )
})

/** Every (view, params) pair the last search sent. */
const calls = () => api.searchInventory.mock.calls

async function find(count) {
  await userEvent.click(screen.getByRole('button', { name: /^find$/i }))
  await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(count))
}

describe('ItemFinder', () => {
  it('by default searches coins and notes, for what has not arrived', async () => {
    // One parcel can hold both, so "Any" asks both views -- and the coins
    // view is everything that is not a note, so the two are every item.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await find(4)
    const asked = calls().map(([view, params]) => `${view}:${params.status}`)
    expect(asked.sort()).toEqual([
      'coins:missing',
      'coins:ordered',
      'currency:missing',
      'currency:ordered',
    ])
  })

  it('finds a parcel by part of its order number, in both views', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.type(screen.getByLabelText(/order number/i), ' 4452 ')
    await find(4)
    expect(calls().every(([, params]) => params.order_number === '4452')).toBe(true)
    expect(calls().some(([view]) => view === 'currency')).toBe(true)
    // The row says which order it is on, so a partial match is checkable.
    expect(await screen.findByText(/114-4452-X · ebay\.com/)).toBeInTheDocument()
  })

  it('shows a whole order, received lines included, under Any status', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />, { reference: vocabularies })
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /status/i }),
      'any',
    )
    await find(2)
    expect(
      calls()
        .map(([view]) => view)
        .sort(),
    ).toEqual(['coins', 'currency'])
    expect(calls().every(([, params]) => !('status' in params))).toBe(true)
  })

  it('finds items in a chosen status, such as received', async () => {
    // The owner looked for an 1857 cent already recorded as received and
    // could not find it. Receiving it twice is still refused by the backend.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />, { reference: vocabularies })
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /status/i }),
      'received',
    )
    await find(2)
    expect(calls().every(([, params]) => params.status === 'received')).toBe(true)
  })

  it('looks a coin up by year and mint once Coins is chosen', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    // Not offered under Any: the currency view would refuse them.
    expect(screen.queryByLabelText(/^year/i)).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /coins/i }))
    await userEvent.type(screen.getByLabelText(/^year/i), '1881')
    await userEvent.type(screen.getByLabelText(/mint/i), 'S')

    await find(2)
    expect(calls().every(([view]) => view === 'coins')).toBe(true)
    const [, params] = calls()[0]
    expect(params).toMatchObject({ year_min: '1881', year_max: '1881', mint: 'S' })
  })

  it('looks a note up by a partial serial number once Currency is chosen', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))
    await userEvent.type(screen.getByLabelText(/serial/i), 'L1234')

    await find(2)
    expect(calls().every(([view]) => view === 'currency')).toBe(true)
    expect(calls()[0][1].serial_number).toBe('L1234')
  })

  it('chooses a denomination by name and searches by its code', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />, { reference: vocabularies })
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /denomination/i }),
      'usd_coin_0_01',
    )
    await find(4)
    expect(calls()[0][1].denomination).toBe('usd_coin_0_01')
  })

  it('searches at once, by id, for an order it was opened on', async () => {
    renderWithProviders(
      <ItemFinder onPick={vi.fn()} orderId={7} initialOrderNumber="27-1234" />,
    )
    expect(screen.getByLabelText(/order number/i)).toHaveValue('27-1234')
    // No Find pressed: a link naming one order goes straight to its items --
    // by id, since a number is neither unique across vendors nor always
    // recorded (code review, 2026-09-23).
    await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(4))
    expect(calls().every(([, params]) => params.purchase_order_id === 7)).toBe(true)
    expect(calls().some(([, params]) => 'order_number' in params)).toBe(false)
  })

  it('finds an order recorded with no number', async () => {
    renderWithProviders(
      <ItemFinder onPick={vi.fn()} orderId={8} initialOrderNumber="" />,
    )
    await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(4))
    expect(calls().every(([, params]) => params.purchase_order_id === 8)).toBe(true)
  })

  it('searches by number once another number is typed over a linked one', async () => {
    renderWithProviders(
      <ItemFinder onPick={vi.fn()} orderId={7} initialOrderNumber="27-1234" />,
    )
    await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(4))
    const box = screen.getByLabelText(/order number/i)
    await userEvent.clear(box)
    await userEvent.type(box, '4452')
    await find(8)
    const typed = calls().slice(4)
    expect(typed.every(([, params]) => params.order_number === '4452')).toBe(true)
    expect(typed.some(([, params]) => 'purchase_order_id' in params)).toBe(false)
  })

  it('repeats the last search when the page says a receipt landed', async () => {
    // Bumps `epoch` the way Receiving does after a receipt, without
    // remounting the finder -- a remount would forget the last search.
    function Page() {
      const [epoch, setEpoch] = useState(0)
      return (
        <>
          <ItemFinder onPick={vi.fn()} epoch={epoch} />
          <button type="button" onClick={() => setEpoch((n) => n + 1)}>
            receipt landed
          </button>
        </>
      )
    }
    renderWithProviders(<Page />)
    await userEvent.type(screen.getByLabelText(/order number/i), '4452')
    await find(4)

    await userEvent.click(screen.getByRole('button', { name: 'receipt landed' }))
    await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(8))
    expect(calls()[7][1].order_number).toBe('4452')
  })

  it('hands the whole chosen row back to the page, as a keyboard-reachable button', async () => {
    const onPick = vi.fn()
    renderWithProviders(<ItemFinder onPick={onPick} />)
    await find(4)
    await userEvent.click(await screen.findByRole('button', { name: /CC-000412/ }))
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ id: 412, item_code: 'CC-000412' }),
    )
  })

  it('drops a response that lands after the kind was switched away', async () => {
    // A search stalls, the operator switches kind before it resolves, and
    // only then does the stale response land. It must never reach the
    // screen as a clickable row from a search already navigated away from.
    const pending = []
    api.searchInventory.mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve)
        }),
    )
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /^find$/i }))
    await waitFor(() => expect(pending).toHaveLength(4))

    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))
    await act(async () => {
      pending.forEach((resolve) => resolve({ rows: [MORGAN], total: 1 }))
    })
    expect(screen.queryByText('CC-000412')).not.toBeInTheDocument()
  })

  it('asks for full pages, and says when more matched than came back', async () => {
    // The endpoint's default of 50 rows cut a big order short silently
    // (code review, 2026-09-23).
    api.searchInventory.mockImplementation((view, params) =>
      Promise.resolve(
        view === 'coins' && params.status === 'ordered'
          ? { rows: [MORGAN], total: 51 }
          : { rows: [], total: 0 },
      ),
    )
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await find(4)
    expect(calls().every(([, params]) => params.limit === 200)).toBe(true)
    expect(await screen.findByText(/50 more match than are shown/)).toBeInTheDocument()
  })

  it('explains the order number field when it has focus', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByLabelText(/order number/i))
    expect(screen.getByText(/part of it is enough/i)).toBeInTheDocument()
  })

  it('opens with focus on Any, so the help shows at once', () => {
    // The owner's request (2026-09-23): the page should explain itself
    // before anything is clicked.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    expect(screen.getByRole('radio', { name: 'Any' })).toHaveFocus()
    expect(screen.getByRole('radiogroup', { name: 'Search for:' })).toBeInTheDocument()
    expect(screen.getByText('Search for')).toBeInTheDocument()
    expect(screen.getByText(/Any: coins and notes together/)).toBeInTheDocument()
  })

  it('explains the kind radio buttons when one is chosen', async () => {
    // The owner found the radios left the help area unchanged (2026-09-23).
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByLabelText(/order number/i))
    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))
    expect(screen.getByText(/one parcel can hold both/)).toBeInTheDocument()
    expect(screen.queryByText(/part of it is enough/i)).toBeNull()
  })
})
