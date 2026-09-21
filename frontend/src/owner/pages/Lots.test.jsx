import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The lot calls, plus the two OfferDialog makes once "Offer for sale..." is
// pressed -- it is the real dialog here, not a stub, because "the same dialog
// an item uses" is the thing being asserted.
vi.mock('../api', () => ({
  api: {
    listLots: vi.fn(),
    createLot: vi.fn(),
    updateLot: vi.fn(),
    deleteLot: vi.fn(),
    listSalesVenues: vi.fn(),
    createOffers: vi.fn(),
  },
}))

import { api } from '../api'
import Lots from './Lots'
import { ApiError } from '../../shared/api'
import { renderWithProviders } from '../../test/helpers'

// One coin in a lot, as `SalesLotMemberOut` sends it.
const MORGAN = {
  inventory_item_id: 7,
  item_code: 'CC-000007',
  title: '1881-S Morgan Dollar',
  cost_basis: '120.00',
  value: '180.00',
}

const PEACE = {
  inventory_item_id: 9,
  item_code: 'CC-000009',
  title: '1923 Peace Dollar',
  cost_basis: '95.00',
  value: null,
}

const WALKER = {
  inventory_item_id: 11,
  item_code: 'CC-000011',
  title: '1943 Walking Liberty Half',
  cost_basis: '40.00',
  value: '60.00',
}

const assembling = {
  id: 1,
  title: 'Three Morgans',
  description: 'A short date run.',
  status: 'assembling',
  version: 1,
  members: [MORGAN],
  cost_basis: '1000.00',
  value: '1400.00',
  unvalued_count: 0,
}

// A lot that was offered and came back unsold: its memberships are released
// rather than deleted, so the coins it held are still listed against it.
const dissolved = {
  ...assembling,
  id: 2,
  title: 'Two Peace Dollars',
  status: 'dissolved',
  version: 4,
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listSalesVenues.mockResolvedValue([])
})

describe('Lots', () => {
  it('lists assembling lots with their running cost basis', async () => {
    api.listLots.mockResolvedValue({
      lots: [
        {
          id: 1,
          title: 'Three Morgans',
          status: 'assembling',
          version: 1,
          cost_basis: '1000.00',
          value: '1400.00',
          members: [],
        },
      ],
    })
    renderWithProviders(<Lots />, { strict: true })
    expect(await screen.findByText('Three Morgans')).toBeVisible()
    expect(screen.getByText('1000.00')).toBeVisible()
  })

  it('sends the version token when membership changes', async () => {
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [assembling] })
    api.updateLot.mockResolvedValue({ ...assembling, version: 2 })
    renderWithProviders(<Lots />, { strict: true })
    await user.click(await screen.findByRole('button', { name: /remove/i }))
    expect(api.updateLot).toHaveBeenCalledWith(
      assembling.id,
      expect.objectContaining({ version: assembling.version }),
    )
  })

  it('offers a lot through the same dialog an item uses', async () => {
    const user = userEvent.setup()
    // Three coins, not the one `assembling` carries. With a single member,
    // "one row for the lot" and "a row per coin" are the same two rows, and
    // the row count below could not tell them apart -- which is exactly the
    // mistake this case exists to catch.
    api.listLots.mockResolvedValue({
      lots: [{ ...assembling, members: [MORGAN, PEACE, WALKER] }],
    })
    renderWithProviders(<Lots />, { strict: true })
    await user.click(await screen.findByRole('button', { name: /^offer/i }))
    expect(await screen.findByRole('dialog', { name: /offer/i })).toBeVisible()
    // The heading row and ONE row for the lot. A lot is one thing however
    // many coins are in it -- `OfferIn` carries one price, one title and one
    // listing number for it -- so a dialog with a row per coin would be
    // asking for four prices the API has nowhere to put.
    expect(within(screen.getByRole('dialog')).getAllByRole('row')).toHaveLength(2)
  })

  it('shows a load failure without blanking the page', async () => {
    // A venues- or lots-load failure taking the page down is the exact bug
    // 872e219 fixed on main on 2026-09-19 ("Stop a refused action taking the
    // console page down with it"). The page must stay usable.
    api.listLots.mockRejectedValue(new ApiError(500, 'boom', {}))
    renderWithProviders(<Lots />, { strict: true })
    expect(await screen.findByText(/boom/)).toBeVisible()
    expect(screen.getByRole('heading', { name: /sales lots/i })).toBeVisible()
  })

  it('re-offers a dissolved lot as a new assembling one', async () => {
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [dissolved] })
    api.createLot.mockResolvedValue({ ...assembling, id: 9 })
    renderWithProviders(<Lots />, { strict: true })
    await user.click(await screen.findByRole('button', { name: /re-offer as a lot/i }))
    expect(api.createLot).toHaveBeenCalledWith(
      expect.objectContaining({ title: dissolved.title }),
    )
  })

  it('puts the dissolved lot’s coins into the new one', async () => {
    // The half the case above cannot see. A re-offer that started an empty
    // lot named after the old one would pass that assertion and leave the
    // operator to re-add every coin by hand.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [dissolved] })
    api.createLot.mockResolvedValue({ ...assembling, id: 9, version: 1, members: [] })
    api.updateLot.mockResolvedValue({ ...assembling, id: 9, version: 2 })
    renderWithProviders(<Lots />, { strict: true })
    await user.click(await screen.findByRole('button', { name: /re-offer as a lot/i }))
    expect(api.updateLot).toHaveBeenCalledWith(
      9,
      expect.objectContaining({ add_item_ids: [MORGAN.inventory_item_id] }),
    )
  })

  it('does not offer an empty lot', async () => {
    // `offering_writes._lot_members` raises `EmptyLot` and the API answers
    // 422, so an Offer button that was live here would only ever produce a
    // refusal after the price had been typed.
    api.listLots.mockResolvedValue({ lots: [{ ...assembling, members: [] }] })
    renderWithProviders(<Lots />, { strict: true })
    expect(await screen.findByRole('button', { name: /^offer/i })).toBeDisabled()
  })
})
