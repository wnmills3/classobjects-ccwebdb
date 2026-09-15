import userEvent from '@testing-library/user-event'
import { act, screen, waitFor } from '@testing-library/react'
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

beforeEach(() => {
  vi.clearAllMocks()
  // Status-aware: by default the component searches `ordered` and `missing`
  // separately and merges them, so a mock that always answered the same way
  // regardless of `status` would hide that it ever asked for the second one.
  api.searchInventory.mockImplementation((_view, params) =>
    Promise.resolve(
      params.status === 'ordered'
        ? {
            rows: [
              { id: 412, item_code: 'CC-000412', description: '1881-S Morgan $1' },
            ],
            total: 1,
          }
        : { rows: [], total: 0 },
    ),
  )
})

async function search() {
  await userEvent.click(screen.getByRole('button', { name: /find/i }))
  await waitFor(() => expect(api.searchInventory).toHaveBeenCalledTimes(2))
  return api.searchInventory.mock.calls[0]
}

describe('ItemFinder', () => {
  it('looks a coin up by denomination, year and mint', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.type(screen.getByLabelText(/year/i), '1881')
    await userEvent.type(screen.getByLabelText(/mint/i), 'S')

    const [view, params] = await search()
    expect(view).toBe('coins')
    expect(params.year_min).toBe('1881')
    expect(params.year_max).toBe('1881')
    expect(params.mint).toBe('S')
  })

  it('chooses a denomination by name and searches by its code', async () => {
    // It was a free-text box, and the filter compares codes: typing "Cent"
    // matched nothing, ever. Choosing from the vocabulary sends the code.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />, { reference: vocabularies })
    expect(screen.getByRole('option', { name: 'Cent' })).toBeInTheDocument()
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /denomination/i }),
      'usd_coin_0_01',
    )

    const [, params] = await search()
    expect(params.denomination).toBe('usd_coin_0_01')
  })

  it('looks a note up by a partial serial number', async () => {
    // The backend filter is an ilike, so a fragment is a legitimate search --
    // reading a whole serial off a banknote through a flip is the exception,
    // not the rule.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))
    await userEvent.type(screen.getByLabelText(/serial/i), 'L1234')

    const [view, params] = await search()
    expect(view).toBe('currency')
    expect(params.serial_number).toBe('L1234')
  })

  it('by default searches only what has not arrived', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await search()
    const statusesQueried = api.searchInventory.mock.calls.map(
      ([, params]) => params.status,
    )
    expect(statusesQueried.sort()).toEqual(['missing', 'ordered'])
  })

  it('by default offers a parcel written off as missing, and nothing received', async () => {
    // `missing` means paid for, not cancelled, never arrived -- and things
    // that never arrived sometimes turn up. Received items appear only when
    // the operator chooses that status.
    api.searchInventory.mockImplementation((_view, params) =>
      Promise.resolve(
        params.status === 'missing'
          ? {
              rows: [
                { id: 500, item_code: 'CC-000500', description: '1899-O Morgan $1' },
              ],
              total: 1,
            }
          : { rows: [], total: 0 },
      ),
    )
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /find/i }))

    expect(await screen.findByText('CC-000500')).toBeInTheDocument()
    expect(
      api.searchInventory.mock.calls.some(([, params]) => params.status === 'received'),
    ).toBe(false)
  })

  it('finds items in a chosen status, such as received', async () => {
    // The owner looked for an 1857 cent that had already been recorded as
    // received and could not find it. Receiving it a second time is still
    // refused by the backend; finding it is not.
    api.searchInventory.mockResolvedValue({
      rows: [{ id: 7648, item_code: 'CC-007649', description: 'Auction #522' }],
      total: 1,
    })
    renderWithProviders(<ItemFinder onPick={vi.fn()} />, { reference: vocabularies })
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: /status/i }),
      'received',
    )
    await userEvent.click(screen.getByRole('button', { name: /find/i }))

    expect(await screen.findByText('CC-007649')).toBeInTheDocument()
    expect(api.searchInventory).toHaveBeenCalledTimes(1)
    expect(api.searchInventory.mock.calls[0][1].status).toBe('received')
  })

  it('hands a chosen result back to the page', async () => {
    const onPick = vi.fn()
    renderWithProviders(<ItemFinder onPick={onPick} />)
    await search()
    await userEvent.click(await screen.findByText('CC-000412'))
    expect(onPick).toHaveBeenCalledWith(412)
  })

  it('drops a coins response that lands after the view was switched away', async () => {
    // Reproduces the reviewer's probe: a coins search stalls, the operator
    // switches to currency before it resolves, and only then does the
    // stale coins response land. It must never reach the screen -- rendering
    // it would leave a clickable row from a search the operator already
    // navigated away from, which `onPick` would report as a currency pick.
    // Two requests go out per default search (`ordered` and `missing`), both
    // of which must be resolved -- and both dropped -- for the view switch to
    // be proven safe.
    const resolveCoins = []
    api.searchInventory.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCoins.push(resolve)
        }),
    )

    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /find/i }))
    await waitFor(() =>
      expect(api.searchInventory).toHaveBeenCalledWith('coins', expect.anything()),
    )
    await waitFor(() => expect(resolveCoins).toHaveLength(2))

    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))

    await act(async () => {
      resolveCoins.forEach((resolve) =>
        resolve({
          rows: [{ id: 412, item_code: 'CC-000412', description: '1881-S Morgan $1' }],
          total: 1,
        }),
      )
    })

    expect(screen.queryByText('CC-000412')).not.toBeInTheDocument()
  })
})
