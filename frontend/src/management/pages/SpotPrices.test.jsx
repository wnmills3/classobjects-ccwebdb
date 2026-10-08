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

  it.each([
    // As a price is written and copied from a quote.
    ['$31.50', '31.50'],
    ['4,012.50', '4012.50'],
    ['$ 4,012.50', '4012.50'],
    [' $1,234,567.8912 ', '1234567.8912'],
    ['0.2875', '0.2875'],
  ])('reads "%s" as the price %s', async (typed, sent) => {
    const user = await open()
    api.recordMetalPrice.mockResolvedValue([SILVER, GOLD, COPPER])

    await user.type(screen.getByLabelText('New price for Gold'), typed)
    await user.click(within(row('Gold')).getByRole('button', { name: 'Record' }))

    expect(api.recordMetalPrice).toHaveBeenCalledWith({
      metal: 'gold',
      price_per_ozt: sent,
    })
  })

  it.each(['abc', '-5', '31.123456', '31.5.0', '$', '31 dollars'])(
    'says why "%s" is not a price, and sends nothing',
    async (typed) => {
      const user = await open()
      const box = screen.getByLabelText('New price for Silver')
      await user.type(box, typed)
      // The button answers: a greyed-out one explains nothing.
      const button = within(row('Silver')).getByRole('button', { name: 'Record' })
      expect(button).toBeEnabled()

      await user.click(button)

      expect(
        screen.getByText(
          'Silver: enter the price of one troy ounce as an amount, like 31.50 ' +
            'or 4,012.50 -- up to four decimal places.',
        ),
      ).toBeVisible()
      expect(api.recordMetalPrice).not.toHaveBeenCalled()
      // What was typed stays, to be corrected.
      expect(box).toHaveValue(typed)
    },
  )

  it('says why with Enter too, and clears it once a price is recorded', async () => {
    const user = await open()
    api.recordMetalPrice.mockResolvedValue([SILVER, GOLD, COPPER])
    const box = screen.getByLabelText('New price for Silver')

    await user.type(box, 'abc{Enter}')
    expect(screen.getByText(/Silver: enter the price of one troy ounce/)).toBeVisible()

    await user.clear(box)
    await user.type(box, '31.50{Enter}')
    await waitFor(() => expect(api.recordMetalPrice).toHaveBeenCalled())
    expect(screen.queryByText(/enter the price of one troy ounce/)).toBeNull()
  })

  it('looks ready for a price: a box that says what it is for, and a live button', async () => {
    await open()
    for (const metal of ['Silver', 'Gold', 'Copper']) {
      const box = screen.getByLabelText(`New price for ${metal}`)
      // Words, not a figure: `0.00` here read as the metal's price.
      expect(box).toHaveAttribute('placeholder', 'type a price')
      expect(box).toBeEnabled()
      // Not greyed out before anything is typed: that read as a dead page.
      expect(within(row(metal)).getByRole('button', { name: 'Record' })).toBeEnabled()
    }
    expect(screen.getByText(/click in the\s+metal.s/)).toBeVisible()
  })

  it.each(['', '   '])(
    'asked to record an empty box ("%s"), says what to type',
    async (typed) => {
      const user = await open()
      const box = screen.getByLabelText('New price for Silver')
      if (typed) await user.type(box, typed)

      await user.click(within(row('Silver')).getByRole('button', { name: 'Record' }))

      expect(
        screen.getByText(/Silver: enter the price of one troy ounce/),
      ).toBeVisible()
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
    expect(screen.getByLabelText('New price for Silver')).toHaveValue('31.50')
  })

  it('greys the buttons out only while a price is being recorded', async () => {
    const user = await open()
    let answer
    api.recordMetalPrice.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      }),
    )

    await user.type(screen.getByLabelText('New price for Silver'), '31.50{Enter}')

    expect(
      within(row('Silver')).getByRole('button', { name: 'Recording...' }),
    ).toBeDisabled()
    // One at a time: a second price is not sent behind the first.
    expect(within(row('Gold')).getByRole('button', { name: 'Record' })).toBeDisabled()

    answer([SILVER, GOLD, COPPER])
    await waitFor(() =>
      expect(within(row('Gold')).getByRole('button', { name: 'Record' })).toBeEnabled(),
    )
  })

  describe('the order of the metals', () => {
    const metal = (code, held) => ({
      ...GOLD,
      metal: code,
      label: code[0].toUpperCase() + code.slice(1),
      fine_ozt_held: held,
    })
    //: As the server sends them: the vocabulary's order, which is not by
    //: how much is held, with the empty ones scattered through it.
    const AS_SENT = [
      metal('silver', '2434.126688'),
      metal('gold', '1.135711'),
      metal('platinum', '0.100000'),
      metal('palladium', null),
      metal('copper', '1021.779622'),
      metal('nickel', '0.000000'),
      metal('zinc', '0.000000'),
      metal('plated', '0.361690'),
    ]
    const shown = () =>
      within(screen.getByRole('table', { name: 'Spot prices' }))
        .getAllByRole('row')
        .slice(1)
        .map((tr) => within(tr).getAllByRole('cell')[0].textContent)

    it('puts the most held first and those with none last', async () => {
      await open(AS_SENT)
      expect(shown()).toEqual([
        'Silver',
        'Copper',
        'Gold',
        'Plated',
        'Platinum',
        // None held -- no weight recorded, or a weight of nothing -- in the
        // order they were sent.
        'Palladium',
        'Nickel',
        'Zinc',
      ])
    })

    it('compares the ounces as amounts, not as text', async () => {
      // As text "9.5" sorts above "1021.78" and "10.2".
      await open([
        metal('gold', '9.5'),
        metal('copper', '1021.78'),
        metal('silver', '10.2'),
      ])
      expect(shown()).toEqual(['Copper', 'Silver', 'Gold'])
    })

    it('keeps that order in the table a recording answers with', async () => {
      const user = await open(AS_SENT)
      api.recordMetalPrice.mockResolvedValue(
        AS_SENT.map((row) =>
          row.metal === 'zinc' ? { ...row, price_per_ozt: '0.0900' } : row,
        ),
      )

      await user.type(screen.getByLabelText('New price for Zinc'), '0.09{Enter}')

      await waitFor(() => expect(cells('Zinc')[1]).toBe('$0.09'))
      // A price does not move a metal up: what is held of it does.
      expect(shown().slice(0, 2)).toEqual(['Silver', 'Copper'])
      expect(shown().at(-1)).toBe('Zinc')
    })
  })

  it('says so when the prices cannot be loaded', async () => {
    api.listMetalPrices.mockRejectedValue(new Error('Not signed in'))
    renderWithProviders(<SpotPrices />)

    expect(await screen.findByText('Not signed in')).toBeVisible()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
