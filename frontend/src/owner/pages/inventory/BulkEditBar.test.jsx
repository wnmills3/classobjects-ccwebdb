import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    bulkEditInventory: vi.fn(),
    // OfferDialog's own calls, reachable once "Offer for sale..." is pressed.
    listSalesVenues: vi.fn(),
    createOffers: vi.fn(),
  },
}))

import { api } from '../../api'
import BulkEditBar from './BulkEditBar'

// What the inventory page has on screen: the current page of results. Only
// these rows can be priced, because only these have been loaded.
const ROWS = [
  {
    id: 1,
    item_code: 'CC-000001',
    source_title: '1935 Silver Certificate $1',
    description: 'Blue seal.',
    total_cost: '12.00',
  },
  {
    id: 2,
    item_code: 'CC-000002',
    source_title: '1957 Silver Certificate $1',
    description: 'Blue seal.',
    total_cost: '9.00',
  },
]

const VENUES = [
  {
    code: 'store',
    name: 'Web store',
    is_own_store: true,
    is_active: true,
    commission_rate: null,
    processing_rate: null,
    processing_fixed: null,
    listing_fee: null,
  },
]

beforeEach(() => {
  vi.resetAllMocks()
  api.listSalesVenues.mockResolvedValue(VENUES)
})

describe('BulkEditBar', () => {
  it('offers to change items for sale only once the server names them', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.bulkEditInventory
      .mockRejectedValueOnce(
        new Error('For sale -- CC-000001: listing #3 at 189.00. A change shows ...'),
      )
      .mockResolvedValueOnce({ updated: 2 })
    render(<BulkEditBar ids={[1, 2]} onApplied={onApplied} onClear={vi.fn()} />)

    expect(screen.queryByRole('checkbox')).toBeNull()
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], { year_start: 1964 })
    expect(await screen.findByText(/CC-000001/)).toBeVisible()

    await user.click(
      screen.getByRole('checkbox', { name: 'Change the items for sale too' }),
    )
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], {
      year_start: 1964,
      acknowledge_for_sale: true,
    })
    expect(onApplied).toHaveBeenCalled()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  // Paper has no metal, and the API has no metal on the currency view.
  it('offers Metal on the coin view', () => {
    render(<BulkEditBar view="coins" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)
    const fields = Array.from(
      screen.getByRole('combobox').querySelectorAll('option'),
    ).map((o) => o.textContent)
    expect(fields).toContain('Metal')
  })

  it('does not offer Metal on the currency view', () => {
    render(
      <BulkEditBar view="currency" ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    const fields = Array.from(
      screen.getByRole('combobox').querySelectorAll('option'),
    ).map((o) => o.textContent)
    expect(fields).not.toContain('Metal')
    expect(fields).toContain('Grade')
  })

  it('offers the selection for sale, and clears it once they are listed', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.createOffers.mockResolvedValue({ listings: [{ id: 21 }, { id: 22 }] })
    render(
      <BulkEditBar ids={[1, 2]} rows={ROWS} onApplied={onApplied} onClear={vi.fn()} />,
    )
    await user.click(screen.getByRole('button', { name: 'Offer for sale...' }))

    await screen.findByRole('option', { name: 'Web store' })
    await user.selectOptions(screen.getByLabelText('Platform'), 'store')
    await user.type(screen.getByLabelText('Price for CC-000001'), '24.00')
    await user.type(screen.getByLabelText('Price for CC-000002'), '18.00')
    await user.click(screen.getByRole('button', { name: 'Offer 2 for sale' }))

    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'store',
      format: 'fixed_price',
      items: [
        {
          item_id: 1,
          price: '24.00',
          title: '1935 Silver Certificate $1',
          description: 'Blue seal.',
          external_id: null,
        },
        {
          item_id: 2,
          price: '18.00',
          title: '1957 Silver Certificate $1',
          description: 'Blue seal.',
          external_id: null,
        },
      ],
    })
    // The same callback the bulk edit uses: the parent drops the selection
    // and refetches, because every offered row's status has changed.
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  // A selection survives paging, and a row from another page has no price,
  // no title and no cost on screen. Dropping it quietly is how a batch
  // silently offers fewer items than the count on the button.
  it('says how many of the selection are not on this page', async () => {
    const user = userEvent.setup()
    render(
      <BulkEditBar
        ids={[1, 2, 5, 6]}
        rows={ROWS}
        onApplied={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Offer for sale...' }))
    expect(
      await screen.findByText(/2 other selected item\(s\) are not on this page/),
    ).toBeVisible()
  })

  it('cannot offer a selection with nothing on this page', () => {
    render(<BulkEditBar ids={[5]} rows={ROWS} onApplied={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Offer for sale...' })).toBeDisabled()
  })

  it('does not offer it for another refusal', async () => {
    const user = userEvent.setup()
    api.bulkEditInventory.mockRejectedValueOnce(new Error('Unknown grade: zz'))
    render(<BulkEditBar ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 1' }))
    expect(await screen.findByText('Unknown grade: zz')).toBeVisible()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})
