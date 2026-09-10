import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
  },
}))

import { api } from '../../api'
import ItemEditForm from './ItemEditForm'

const item = {
  id: 12,
  item_code: 'C-012',
  description: 'Mercury Dime',
  reviewed: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getInventoryItem.mockResolvedValue(item)
  api.setItemReview.mockResolvedValue({ reviewed: ['description'] })
})

describe('ItemEditForm', () => {
  it('loads the item it was given', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(api.getInventoryItem).toHaveBeenCalledWith(12)
  })

  it('offers a confirmed checkbox per reviewable field', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.getAllByLabelText(/confirmed/i).length).toBeGreaterThan(0)
  })

  it('records a field as reviewed when its box is ticked', async () => {
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    // By role rather than by label: the label's title also contains
    // "confirmed", so a text query can return the label instead of the input.
    const box = screen.getAllByRole('checkbox')[0]
    expect(box).not.toBeChecked()
    await user.click(box)

    await waitFor(() =>
      // replace:true -- unticking must remove the record, not leave a
      // confirmation nobody stands behind.
      expect(api.setItemReview).toHaveBeenCalledWith(12, expect.any(Array), true),
    )
  })

  it('reports a failed load rather than showing an empty form', async () => {
    api.getInventoryItem.mockRejectedValue(new Error('item 12 is gone'))
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText('item 12 is gone')).toBeInTheDocument()
  })
})

describe('No sales tax charged', () => {
  const taxed = {
    ...item,
    version: 3,
    item_cost: '179.00',
    shipping_cost: '0.00',
    sales_tax: '11.37',
    total_cost: '190.37',
    tax_rate: '0.0635',
    tax_includes_shipping: true,
    default_tax_rate: '0.0635',
  }
  const untaxed = {
    ...taxed,
    sales_tax: '0.00',
    total_cost: '179.00',
    tax_rate: '0.0000',
  }

  async function saveAfterClicking(loaded) {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue(loaded)
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await user.click(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    )
    await user.click(screen.getByRole('button', { name: /save/i }))
  }

  it('sets the rate to zero when ticked', async () => {
    await saveAfterClicking(taxed)
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0', version: 3 }),
      ),
    )
  })

  it('shows an item already recorded as untaxed as ticked', async () => {
    api.getInventoryItem.mockResolvedValue(untaxed)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    ).toBeChecked()
  })

  it('ticking and unticking again leaves the rate as it was', async () => {
    const user = userEvent.setup()
    // Stamped at 6.35% when bought; the setting has since moved to 7%.
    api.getInventoryItem.mockResolvedValue({ ...taxed, default_tax_rate: '0.0700' })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const box = await screen.findByRole('checkbox', { name: /no sales tax charged/i })

    await user.click(box)
    await user.click(box)

    // Back to the rate it was bought at, not today's setting -- so nothing
    // changed and there is nothing to save.
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })

  it('restores the configured rate when unticked, not a rate of its own', async () => {
    await saveAfterClicking({ ...untaxed, default_tax_rate: '0.0700' })
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0.0700' }),
      ),
    )
  })
})
