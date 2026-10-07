import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listMetalPrices: vi.fn(),
    recordMetalPrice: vi.fn(),
  },
}))

import { api } from '../api'
import { renderWithProviders } from '../../test/helpers'
import SpotPrices from './SpotPrices'

const SILVER = {
  metal: 'silver',
  label: 'Silver',
  price_per_ozt: '30.0000',
  quoted_at: '2026-10-01T15:00:00Z',
  source: 'manual',
  fine_ozt_held: '21.773400',
  melt_value: '653.20',
}
//: Held, never quoted: ounces, and no value put on them.
const GOLD = {
  metal: 'gold',
  label: 'Gold',
  price_per_ozt: null,
  quoted_at: null,
  source: null,
  fine_ozt_held: '1.000000',
  melt_value: null,
}
//: Quoted in cents an ounce, and nothing held.
const COPPER = {
  metal: 'copper',
  label: 'Copper',
  price_per_ozt: '0.2875',
  quoted_at: '2026-10-01T15:00:00Z',
  source: 'manual',
  fine_ozt_held: null,
  melt_value: null,
}

const row = (label) => screen.getByRole('row', { name: new RegExp(`^${label}`) })
const cells = (label) =>
  within(row(label))
    .getAllByRole('cell')
    .map((cell) => cell.textContent)

async function open(rows = [SILVER, GOLD, COPPER]) {
  api.listMetalPrices.mockResolvedValue(rows)
  renderWithProviders(<SpotPrices />)
  await screen.findByRole('table', { name: 'Spot prices' })
  return userEvent.setup()
}

beforeEach(() => vi.resetAllMocks())

describe('SpotPrices', () => {
  it('shows each metal with its price, what is held and what that melts for', async () => {
    await open()

    expect(cells('Silver').slice(0, 5)).toEqual([
      'Silver',
      '$30.00',
      new Date('2026-10-01T15:00:00Z').toLocaleString(),
      '21.773 ozt',
      '$653.20',
    ])
  })

  it('says a metal was never quoted, and puts no value on what is held of it', async () => {
    await open()
    // Not "$0.00": not quoted is not worth nothing.
    expect(cells('Gold').slice(1, 5)).toEqual(['--', 'never', '1.000 ozt', '--'])
  })

  it('shows a price quoted in fractions of a cent in full', async () => {
    await open()
    expect(cells('Copper').slice(1, 5)).toEqual([
      '$0.2875',
      new Date('2026-10-01T15:00:00Z').toLocaleString(),
      '--',
      '--',
    ])
  })

  it('totals the melt value of what is quoted', async () => {
    await open([SILVER, GOLD, { ...COPPER, fine_ozt_held: '16', melt_value: '4.60' }])
    expect(
      screen.getByRole('row', { name: /Melt value of everything quoted/ }),
    ).toHaveTextContent('$657.80')
  })

  it('shows no total when nothing is valued', async () => {
    await open([GOLD])
    expect(screen.queryByText(/Melt value of everything quoted/)).toBeNull()
  })

  it('records a new price and shows the table the server answers with', async () => {
    const user = await open()
    api.recordMetalPrice.mockResolvedValue([
      SILVER,
      {
        ...GOLD,
        price_per_ozt: '2650.0000',
        quoted_at: '2026-10-07T17:00:00Z',
        melt_value: '2650.00',
      },
      COPPER,
    ])

    await user.type(screen.getByLabelText('New price for Gold'), ' 2650 ')
    await user.click(within(row('Gold')).getByRole('button', { name: 'Record' }))

    expect(api.recordMetalPrice).toHaveBeenCalledTimes(1)
    // The metal's code and the price as text, trimmed: never a float.
    expect(api.recordMetalPrice).toHaveBeenCalledWith({
      metal: 'gold',
      price_per_ozt: '2650',
    })
    await waitFor(() => expect(cells('Gold')[1]).toBe('$2,650.00'))
    expect(cells('Gold')[4]).toBe('$2,650.00')
    expect(screen.getByLabelText('New price for Gold')).toHaveValue('')
    expect(screen.getByRole('status')).toHaveTextContent(
      'Gold recorded at $2,650.00 an ounce.',
    )
  })

  it('records with Enter in the box', async () => {
    const user = await open()
    api.recordMetalPrice.mockResolvedValue([SILVER, GOLD, COPPER])

    await user.type(screen.getByLabelText('New price for Silver'), '31.50{Enter}')

    expect(api.recordMetalPrice).toHaveBeenCalledWith({
      metal: 'silver',
      price_per_ozt: '31.50',
    })
  })

  it.each(['', 'abc', '-5', '31.123456', '1,000'])(
    'will not record "%s"',
    async (typed) => {
      const user = await open()
      const box = screen.getByLabelText('New price for Silver')
      if (typed) await user.type(box, typed)
      expect(
        within(row('Silver')).getByRole('button', { name: 'Record' }),
      ).toBeDisabled()

      await user.type(box, '{Enter}')
      expect(api.recordMetalPrice).not.toHaveBeenCalled()
    },
  )

  it('keeps the price typed, and the table as it was, when the server refuses', async () => {
    const user = await open()
    api.recordMetalPrice.mockRejectedValue(new Error('Unknown metal: silver'))

    await user.type(screen.getByLabelText('New price for Silver'), '31.50')
    await user.click(within(row('Silver')).getByRole('button', { name: 'Record' }))

    expect(await screen.findByText('Unknown metal: silver')).toBeVisible()
    expect(screen.getByLabelText('New price for Silver')).toHaveValue('31.50')
    expect(cells('Silver')[1]).toBe('$30.00')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it("types in one metal's box without touching another's", async () => {
    const user = await open()
    await user.type(screen.getByLabelText('New price for Silver'), '31.50')

    expect(screen.getByLabelText('New price for Gold')).toHaveValue('')
    expect(within(row('Gold')).getByRole('button', { name: 'Record' })).toBeDisabled()
    expect(within(row('Silver')).getByRole('button', { name: 'Record' })).toBeEnabled()
  })

  it('says so when the prices cannot be loaded', async () => {
    api.listMetalPrices.mockRejectedValue(new Error('Not signed in'))
    renderWithProviders(<SpotPrices />)

    expect(await screen.findByText('Not signed in')).toBeVisible()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
