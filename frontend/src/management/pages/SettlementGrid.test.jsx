import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: { settleAuction: vi.fn(), listStorageLocations: vi.fn() },
}))

import { api } from '../api'
import SettlementGrid from './SettlementGrid'
import { ApiError } from '../../shared/api'
import { renderWithProviders } from '../../test/helpers'

const FEE_KINDS = [{ code: 'commission', label: 'Commission', is_active: true }]

function reference() {
  return { tables: { sales_fee_kind: FEE_KINDS }, load: vi.fn(), invalidate: vi.fn() }
}

function listing(overrides = {}) {
  return {
    id: 101,
    item_id: null,
    item_code: null,
    item_title: '1881-S Morgan Dollar',
    venue: 'heritage',
    venue_name: 'Heritage Auctions',
    format: 'auction',
    status: 'active',
    price: '10.00',
    currency: 'USD',
    quantity_available: 1,
    title: '1881-S Morgan Dollar',
    description: '',
    external_id: null,
    external_url: null,
    listed_at: '2026-09-01T00:00:00Z',
    ended_at: null,
    paused_by_listing_id: null,
    sales_lot_id: 5,
    member_count: 1,
    cost_basis: '120.00',
    version: 1,
    ...overrides,
  }
}

function auctionLot(overrides = {}) {
  return {
    id: 201,
    lot_number: '1',
    reserve: '20.00',
    result: null,
    hammer_price: null,
    buyer: null,
    listing: listing(),
    ...overrides,
  }
}

function closedAuction(overrides = {}) {
  return {
    id: 1,
    venue: 'heritage',
    venue_name: 'Heritage Auctions',
    title: 'September Signature Sale',
    external_id: 'SIG-2026-09',
    starts_at: null,
    ends_at: null,
    status: 'closed',
    consigned_on: null,
    notes: null,
    version: 1,
    lots: [auctionLot(), auctionLot({ id: 202, lot_number: '2', reserve: '15.00' })],
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listStorageLocations.mockResolvedValue([
    { id: 9, label: 'Home safe', kind: 'home' },
  ])
})

describe('SettlementGrid', () => {
  it('sends the money the owner typed, as strings, and shows totals for display only', async () => {
    const user = userEvent.setup()
    const auction = closedAuction()
    const onSettled = vi.fn()
    api.settleAuction.mockResolvedValue({
      auction: { ...auction, status: 'settled' },
      orders: [{ id: 1 }],
    })
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse={false} onSettled={onSettled} />,
      { strict: true, reference: reference() },
    )

    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    await user.type(screen.getByLabelText('Hammer price for lot 1'), '150.00')
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'amy')
    await user.selectOptions(screen.getByLabelText('Result for lot 2'), 'unsold')

    await user.type(screen.getByLabelText('Commission fee for amy'), '24.00')

    // Displayed totals, computed in whole cents for display only.
    expect(screen.getByText('150.00')).toBeVisible() // gross
    expect(screen.getByText('24.00')).toBeVisible() // fees
    expect(screen.getByText('126.00')).toBeVisible() // net
    expect(screen.getByText('240.00')).toBeVisible() // cost basis: 120 + 120

    await user.click(screen.getByRole('button', { name: /^settle/i }))
    const dialog = screen.getByRole('dialog', { name: /settle/i })
    await user.click(within(dialog).getByRole('button', { name: 'Settle' }))

    expect(api.settleAuction).toHaveBeenCalledWith(auction.id, {
      lines: [
        {
          auction_lot_id: 201,
          result: 'sold',
          hammer_price: '150.00',
          buyer_username: 'amy',
        },
        {
          auction_lot_id: 202,
          result: 'unsold',
          hammer_price: null,
          buyer_username: null,
        },
      ],
      fees: [
        { buyer_username: 'amy', fees: [{ kind: 'commission', amount: '24.00' }] },
      ],
    })
    expect(onSettled).toHaveBeenCalledWith(
      expect.objectContaining({
        auction: expect.objectContaining({ status: 'settled' }),
      }),
    )
  })

  it('marks every offending lot from a structured refusal, not just the first', async () => {
    // The console shows a grid, and fixing one problem at a round trip is
    // miserable -- every problem lot is named at once.
    const user = userEvent.setup()
    const auction = closedAuction()
    api.settleAuction.mockRejectedValue(
      new ApiError(
        409,
        'auction #1 cannot be settled: lot 1 has no result; lot 2 has no result',
        {
          detail:
            'auction #1 cannot be settled: lot 1 has no result; lot 2 has no result',
          refused: [
            { reason: 'lot 1 has no result', lot_number: '1' },
            { reason: 'lot 2 has no result', lot_number: '2' },
          ],
        },
      ),
    )
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse={false} onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )

    await user.click(screen.getByRole('button', { name: /^settle/i }))
    const dialog = screen.getByRole('dialog', { name: /settle/i })
    await user.click(within(dialog).getByRole('button', { name: 'Settle' }))

    // On each lot's own row, not in the list of problems that name no lot:
    // the same words would be visible there too, so the row is what proves it.
    await screen.findByText('lot 1 has no result')
    for (const number of ['1', '2']) {
      const row = screen.getByLabelText(`Result for lot ${number}`).closest('tr')
      expect(row).toHaveClass('error')
      expect(within(row).getByText(`lot ${number} has no result`)).toBeVisible()
    }
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('does not ask for a return location off an auction house', () => {
    const auction = closedAuction()
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse={false} onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )
    expect(screen.queryByLabelText(/return unsold/i)).toBeNull()
  })

  it('asks for a return location at an auction house', () => {
    const auction = closedAuction()
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )
    expect(screen.getByLabelText(/return unsold/i)).toBeVisible()
  })

  it('offers to add a return location that is not yet recorded', async () => {
    const auction = closedAuction()
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )
    const picker = screen.getByLabelText(/return unsold/i)
    expect(
      within(picker).getByRole('option', { name: '+ Add a location...' }),
    ).toBeInTheDocument()
    expect(
      await within(picker).findByRole('option', { name: 'Home safe' }),
    ).toBeInTheDocument()
  })

  it('sends nothing until a required return location is chosen, and says why', async () => {
    // `app.auctions.settle` refuses when the house still holds something
    // and a non-sold lot has nowhere to come back to -- the same
    // `auction.consigned_on` predicate `ReturnLocationConfirm` asks about
    // in `Auctions.jsx`. "Settle..." stays live, as every console button
    // does: a press without the location opens nothing and names it.
    const user = userEvent.setup()
    const auction = closedAuction({ consigned_on: '2026-10-01' })
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )

    const settleButton = screen.getByRole('button', { name: /^settle/i })
    // Nothing marked non-sold yet, so nothing needs to come home.
    expect(settleButton).toBeEnabled()

    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'unsold')
    expect(settleButton).toBeEnabled()
    await user.click(settleButton)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(
      screen.getByText(/choose where unsold and withdrawn items come back to/i, {
        selector: 'p',
      }),
    ).toHaveClass('error')

    // Offered once the picker has read the storage locations.
    await screen.findByRole('option', { name: 'Home safe' })
    await user.selectOptions(
      screen.getByLabelText('Return unsold and withdrawn items to'),
      '9',
    )
    await user.click(settleButton)
    const dialog = screen.getByRole('dialog', { name: /settle/i })
    expect(within(dialog).getByRole('button', { name: 'Settle' })).toBeEnabled()
    expect(api.settleAuction).not.toHaveBeenCalled()
  })

  it('groups the settlement grid buyers case-insensitively, keeping the first spelling', async () => {
    // `app.auctions._buyer_key` is `.strip().casefold()`, so `amy` and
    // `Amy` are one buyer server-side; two fee sub-tables here would have
    // produced an unexplainable "fees for Amy are given twice" refusal.
    const user = userEvent.setup()
    const auction = closedAuction()
    api.settleAuction.mockResolvedValue({
      auction: { ...auction, status: 'settled' },
      orders: [{ id: 1 }],
    })
    renderWithProviders(
      <SettlementGrid auction={auction} isAuctionHouse={false} onSettled={vi.fn()} />,
      { strict: true, reference: reference() },
    )

    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    await user.type(screen.getByLabelText('Hammer price for lot 1'), '100.00')
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'amy')
    await user.selectOptions(screen.getByLabelText('Result for lot 2'), 'sold')
    await user.type(screen.getByLabelText('Hammer price for lot 2'), '50.00')
    await user.type(screen.getByLabelText('Buyer for lot 2'), 'Amy')

    // One fee sub-table, not two.
    expect(screen.getByLabelText('Commission fee for amy')).toBeVisible()
    expect(screen.queryByLabelText('Commission fee for Amy')).toBeNull()

    await user.type(screen.getByLabelText('Commission fee for amy'), '10.00')
    await user.click(screen.getByRole('button', { name: /^settle/i }))
    const dialog = screen.getByRole('dialog', { name: /settle/i })
    await user.click(within(dialog).getByRole('button', { name: 'Settle' }))

    const [, payload] = api.settleAuction.mock.calls[0]
    // Each lot's own line keeps whatever the owner typed on that row...
    expect(payload.lines.map((l) => l.buyer_username)).toEqual(['amy', 'Amy'])
    // ...but the fees are one group, spelled the way the first sold lot
    // typed it -- the same "first spelling wins" rule `_BuyerGroup.username`
    // uses server-side for the customer record.
    expect(payload.fees).toEqual([
      { buyer_username: 'amy', fees: [{ kind: 'commission', amount: '10.00' }] },
    ])
  })

  it('totals only the fees of buyers still in the grid, which are the ones sent', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <SettlementGrid
        auction={closedAuction()}
        isAuctionHouse={false}
        onSettled={vi.fn()}
      />,
      { reference: reference() },
    )
    const total = (name) =>
      screen.getByText(name, { selector: 'dt' }).nextElementSibling.textContent

    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    await user.type(screen.getByLabelText('Hammer price for lot 1'), '150.00')
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'amy')
    await user.type(screen.getByLabelText('Commission fee for amy'), '24.00')
    expect(total('Fees')).toBe('24.00')

    // The buyer's name is corrected: the fee typed under the old spelling
    // belongs to nobody in the grid and is not sent.
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'b')
    expect(screen.getByLabelText('Commission fee for amyb')).toHaveValue('')
    expect(total('Fees')).toBe('0.00')
    expect(total('Net')).toBe('150.00')

    // The lot did not sell after all: its buyer's fees leave the total too.
    await user.type(screen.getByLabelText('Commission fee for amyb'), '9.00')
    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'unsold')
    expect(total('Fees')).toBe('0.00')
  })

  it('names fees left under an old buyer, and settles nothing until they are dealt with', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <SettlementGrid
        auction={closedAuction()}
        isAuctionHouse={false}
        onSettled={vi.fn()}
      />,
      { reference: reference() },
    )
    const stranded = /Fees are typed for a buyer no longer on a sold lot/

    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    await user.type(screen.getByLabelText('Hammer price for lot 1'), '150.00')
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'amy')
    await user.selectOptions(screen.getByLabelText('Result for lot 2'), 'unsold')
    await user.type(screen.getByLabelText('Commission fee for amy'), '24.00')
    // Fees under a buyer in the grid are not stranded.
    expect(screen.queryByText(stranded)).toBeNull()

    await user.type(screen.getByLabelText('Buyer for lot 1'), 'b')

    // Named, with the old buyer and the amount, so it is not silently lost.
    expect(screen.getByText(stranded)).toHaveTextContent('amy (Commission 24.00)')

    // Settle stays pressable, and a press says why and opens nothing.
    const settleButton = screen.getByRole('button', { name: /^settle/i })
    expect(settleButton).toBeEnabled()
    await user.click(settleButton)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(
      screen.getByText(/Move or discard the fees typed for a buyer no longer/),
    ).toHaveClass('error')
    expect(api.settleAuction).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Discard these fees' }))
    expect(screen.queryByText(stranded)).toBeNull()
    await user.click(settleButton)
    expect(screen.getByRole('dialog', { name: /settle/i })).toBeVisible()
  })

  it('stops naming the fees once the buyer is named as they were typed', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <SettlementGrid
        auction={closedAuction()}
        isAuctionHouse={false}
        onSettled={vi.fn()}
      />,
      { reference: reference() },
    )
    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    await user.type(screen.getByLabelText('Buyer for lot 1'), 'amy')
    await user.type(screen.getByLabelText('Commission fee for amy'), '24.00')
    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'unsold')
    expect(screen.getByText(/amy \(Commission 24\.00\)/)).toBeVisible()

    // Sold after all: the fees are the buyer's again, and are sent.
    await user.selectOptions(screen.getByLabelText('Result for lot 1'), 'sold')
    expect(screen.queryByText(/amy \(Commission 24\.00\)/)).toBeNull()
    expect(screen.getByLabelText('Commission fee for amy')).toHaveValue('24.00')
  })
})
