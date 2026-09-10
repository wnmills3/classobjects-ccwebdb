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
