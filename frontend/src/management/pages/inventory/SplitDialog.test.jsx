import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { splitItem: vi.fn() },
}))

import { api } from '../../api'
import { FIELD_HELP } from '../../fieldHelp'
import { renderWithProviders } from '../../../test/helpers'
import SplitDialog from './SplitDialog'

// The nine proof sets of order 26-12472-81162, bought as one listing.
const LOT = {
  id: 41,
  item_code: 'CC-000041',
  source_title: '9 Set Lot of U.S. Proof & Mint Sets ORIGINAL',
  description: 'US Proof Set',
  year_start: 1980,
  piece_count: 9,
  item_cost: '63.54',
  shipping_cost: '0.00',
  sale_state: [],
}

const RESULT = {
  parent_item_code: 'CC-000041',
  parent_total_cost: '63.54',
  allocated_total_cost: '63.54',
  pieces: [{ item_code: 'CC-000042' }, { item_code: 'CC-000043' }],
}

const descriptions = () => screen.getAllByLabelText(/^Description of piece/)

function open(item = LOT) {
  const onSplit = vi.fn()
  renderWithProviders(<SplitDialog item={item} onSplit={onSplit} onClose={vi.fn()} />)
  return onSplit
}

beforeEach(() => {
  vi.clearAllMocks()
  api.splitItem.mockResolvedValue(RESULT)
})

describe('SplitDialog', () => {
  it('starts with a row per piece the lot records, each costed', () => {
    open()
    expect(descriptions()).toHaveLength(9)
    expect(descriptions()[8]).toHaveValue('US Proof Set')
    // 9 x 7.06 is exactly the lot's 63.54.
    expect(screen.getAllByText('7.06')).toHaveLength(9)
  })

  it('sends each piece its own description and year, keeping the seller title', async () => {
    const user = userEvent.setup()
    const onSplit = open({ ...LOT, piece_count: 2 })
    await user.clear(descriptions()[0])
    await user.type(descriptions()[0], '1980 US Proof Set')
    await user.clear(screen.getByLabelText('Year of piece 2'))
    await user.type(screen.getByLabelText('Year of piece 2'), '1981')
    await user.click(screen.getByRole('button', { name: 'Split into 2 pieces' }))

    expect(api.splitItem).toHaveBeenCalledWith(41, {
      mode: 'equal',
      pieces: [
        {
          source_title: LOT.source_title,
          piece_count: 1,
          description: '1980 US Proof Set',
          year_start: 1980,
        },
        {
          source_title: LOT.source_title,
          piece_count: 1,
          description: 'US Proof Set',
          year_start: 1981,
        },
      ],
    })
    await waitFor(() => expect(onSplit).toHaveBeenCalledWith(RESULT))
  })

  it('divides by value only once every piece has one', async () => {
    const user = userEvent.setup()
    open({ ...LOT, piece_count: 2, item_cost: '91.00' })
    await user.selectOptions(screen.getByRole('combobox'), 'relative')
    const button = screen.getByRole('button', { name: 'Split into 2 pieces' })
    expect(button).toBeDisabled()
    expect(screen.getByText(/needs a value for every piece/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Value of piece 1'), '0.01')
    await user.type(screen.getByLabelText('Value of piece 2'), '0.50')
    // 91.00 in the proportion 1:50.
    expect(screen.getByText('1.78')).toBeInTheDocument()
    expect(screen.getByText('89.21')).toBeInTheDocument()
    await user.click(button)
    const sent = api.splitItem.mock.calls[0][1]
    expect(sent.mode).toBe('relative')
    expect(sent.pieces.map((piece) => piece.relative_value)).toEqual(['0.01', '0.50'])
  })

  it('grows and shrinks the rows with Pieces, which can be retyped', async () => {
    const user = userEvent.setup()
    open({ ...LOT, piece_count: 2 })
    const pieces = screen.getByRole('spinbutton')
    await user.clear(pieces)
    await user.type(pieces, '6')
    expect(descriptions()).toHaveLength(6)
    await user.clear(pieces)
    await user.type(pieces, '3')
    expect(descriptions()).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Split into 3 pieces' })).toBeEnabled()
  })

  it('refuses one piece', async () => {
    const user = userEvent.setup()
    open({ ...LOT, piece_count: 2 })
    const pieces = screen.getByRole('spinbutton')
    await user.clear(pieces)
    await user.type(pieces, '1')
    expect(screen.getByRole('button', { name: 'Split into 1 pieces' })).toBeDisabled()
    expect(screen.getByText(/at least two pieces/)).toBeInTheDocument()
  })

  it('asks before splitting a listed lot, and says so to the server', async () => {
    const user = userEvent.setup()
    open({
      ...LOT,
      piece_count: 2,
      sale_state: [{ kind: 'listing', id: 3, text: 'offered on eBay' }],
    })
    const button = screen.getByRole('button', { name: 'Split into 2 pieces' })
    expect(button).toBeDisabled()
    await user.click(screen.getByLabelText('End the offer and split it'))
    await user.click(button)
    expect(api.splitItem.mock.calls[0][1].acknowledge_for_sale).toBe(true)
  })

  it('shows the refusal and stays open', async () => {
    const user = userEvent.setup()
    api.splitItem.mockRejectedValue(new Error('CC-000041 appears in an order'))
    const onSplit = open({ ...LOT, piece_count: 2 })
    await user.click(screen.getByRole('button', { name: 'Split into 2 pieces' }))
    expect(await screen.findByText('CC-000041 appears in an order')).toBeInTheDocument()
    expect(onSplit).not.toHaveBeenCalled()
  })

  it('explains every control, in both modes', async () => {
    const user = userEvent.setup()
    open({ ...LOT, piece_count: 2 })
    await user.selectOptions(screen.getByRole('combobox'), 'relative')
    const bare = [...document.querySelectorAll('dialog input, dialog select')]
      .filter((control) => control.type !== 'checkbox')
      .map((control) => control.closest('[data-help]')?.dataset.help)
      .filter((key) => !key || !FIELD_HELP[key])
    expect(bare).toEqual([])
  })
})
