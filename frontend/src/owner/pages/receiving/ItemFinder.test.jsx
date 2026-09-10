import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({ api: { searchInventory: vi.fn() } }))

import { api } from '../../api'
import ItemFinder from './ItemFinder'
import { renderWithProviders } from '../../../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
  api.searchInventory.mockResolvedValue({
    rows: [{ id: 412, item_code: 'CC-000412', description: '1881-S Morgan $1' }],
    total: 1,
  })
})

async function search() {
  await userEvent.click(screen.getByRole('button', { name: /find/i }))
  await waitFor(() => expect(api.searchInventory).toHaveBeenCalled())
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

  it('only ever offers things that have not arrived', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    const [, params] = await search()
    expect(params.status).toBe('ordered')
  })

  it('hands a chosen result back to the page', async () => {
    const onPick = vi.fn()
    renderWithProviders(<ItemFinder onPick={onPick} />)
    await search()
    await userEvent.click(await screen.findByText('CC-000412'))
    expect(onPick).toHaveBeenCalledWith(412)
  })
})
