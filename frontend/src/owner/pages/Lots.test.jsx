import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
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

  it('says when it is showing only the newest lots', async () => {
    api.listLots.mockResolvedValue({ lots: [assembling], total: 250 })
    renderWithProviders(<Lots />, { strict: true })
    expect(await screen.findByText('Showing the newest 1 of 250 lots.')).toBeVisible()
  })

  it('says nothing about paging when every lot is shown', async () => {
    api.listLots.mockResolvedValue({ lots: [assembling], total: 1 })
    renderWithProviders(<Lots />, { strict: true })
    await screen.findByText(assembling.title)
    expect(screen.queryByText(/Showing the newest/)).not.toBeInTheDocument()
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

  it('discards a lot through its own question, and says the coins are safe', async () => {
    // The only destructive action on the page, and `api.deleteLot` had no
    // assertion anywhere in the repository. `delete_lot` refuses a lot that
    // was ever offered, so what this can destroy is an afternoon of
    // assembling -- which is worth both a question and a confirmation that
    // names what was NOT destroyed.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [assembling] })
    api.deleteLot.mockResolvedValue(null)
    renderWithProviders(<Lots />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'Discard...' }))
    const dialog = screen.getByRole('dialog', {
      name: 'Discard the lot Three Morgans?',
    })
    await user.click(within(dialog).getByRole('button', { name: 'Discard the lot' }))

    expect(api.deleteLot).toHaveBeenCalledWith(assembling.id)
    expect(
      await screen.findByText('Three Morgans is discarded. Its coins are untouched.'),
    ).toBeVisible()
  })

  it('starts a new lot from its wording alone', async () => {
    // `SalesLotIn` takes a title and a description and nothing else, and the
    // page then says what the API called it -- not what was typed -- so a
    // title the server normalised is the one shown.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [] })
    api.createLot.mockResolvedValue({
      ...assembling,
      id: 9,
      title: 'Four Peace Dollars',
      members: [],
    })
    renderWithProviders(<Lots />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New lot...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new lot' })
    await user.type(within(dialog).getByLabelText('Title'), 'Four Peace Dollars')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.createLot).toHaveBeenCalledWith({
      title: 'Four Peace Dollars',
      description: '',
    })
    expect(
      await screen.findByText(
        'Four Peace Dollars is assembling. Add coins to it from the inventory pages.',
      ),
    ).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('keeps the form open, with the refusal, when a save is turned down', async () => {
    // This is the case that pins `LotForm`'s `mounted` ref. The console runs
    // in StrictMode, which invokes every effect setup, cleanup, setup on
    // mount: a ref left at `useRef(true)` and only disarmed in its cleanup
    // is false for the rest of the dialog's life, so `if (!mounted.current)
    // return` in the catch below swallows the message and the operator sees
    // a Save that did nothing at all. That exact trap has silenced a
    // save-failure message on `main` before, which is why this renders with
    // `strict: true` -- the harness does not by default.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [] })
    api.createLot.mockRejectedValue(
      new ApiError(409, 'A lot called that is already assembling', {}),
    )
    renderWithProviders(<Lots />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New lot...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new lot' })
    await user.type(within(dialog).getByLabelText('Title'), 'Three Morgans')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(
      await within(dialog).findByText('A lot called that is already assembling'),
    ).toBeVisible()
    // Still open, and still holding what was typed: the refusal is something
    // to react to, and closing the form would take the work away first.
    expect(screen.getByRole('dialog', { name: 'Start a new lot' })).toBeVisible()
    expect(within(dialog).getByLabelText('Title')).toHaveValue('Three Morgans')
  })

  it('does not send a lot with no title', async () => {
    // `SalesLotIn.title` is `min_length=1`, and the API's refusal for a blank
    // one is a schema complaint about a Decimal-shaped constraint -- true,
    // and no help to someone who has not typed anything yet.
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [] })
    renderWithProviders(<Lots />, { strict: true })

    await user.click(await screen.findByRole('button', { name: 'New lot...' }))
    const dialog = screen.getByRole('dialog', { name: 'Start a new lot' })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(within(dialog).getByText(/a lot needs a title/i)).toBeVisible()
    expect(api.createLot).not.toHaveBeenCalled()
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
