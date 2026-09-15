import userEvent from '@testing-library/user-event'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    searchInventory: vi.fn(),
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
    bulkEditInventory: vi.fn(),
  },
}))

import { api } from '../api'
import { adminAuth, renderWithProviders } from '../../test/helpers'
import { InventoryCurrency } from './Inventory'

// A full page of two-line rows. The bug this guards against only shows with
// a real page: the edit form mounted after fifty rows and the pager, below
// the fold, and the click on an item code looked like it did nothing.
const rows = Array.from({ length: 50 }, (_, i) => ({
  id: i + 1,
  item_code: `CC-${String(i + 1).padStart(6, '0')}`,
  description: 'A note',
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.searchInventory.mockResolvedValue({
    rows,
    total: 900,
    facets: {},
    issues: {},
    sortable: [],
  })
  api.getInventoryItem.mockResolvedValue({
    id: 7,
    item_code: 'CC-000007',
    item_kind: 'currency',
    reviewed: [],
  })
})

async function openItem(user) {
  renderWithProviders(<InventoryCurrency />, {
    auth: adminAuth(),
    route: '/inventory/currency',
  })
  await user.click(await screen.findByRole('button', { name: 'CC-000007' }))
  return screen.findByRole('heading', { name: 'CC-000007' })
}

describe('Inventory item editor', () => {
  it('opens in a modal dialog, not at the foot of the page', async () => {
    const user = userEvent.setup()
    const heading = await openItem(user)

    const dialog = heading.closest('dialog')
    expect(dialog).not.toBeNull()
    expect(dialog).toHaveAttribute('open')
    expect(api.getInventoryItem).toHaveBeenCalledWith(7)
  })

  it('closes from its Close button', async () => {
    const user = userEvent.setup()
    const heading = await openItem(user)
    // Otherwise "no dialog afterwards" also passes when there never was one.
    expect(heading.closest('dialog')).not.toBeNull()

    await user.click(screen.getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(document.querySelector('dialog')).toBeNull())
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const heading = await openItem(user)

    // What the browser fires on Escape. Left to its default the dialog would
    // close itself while React still thought an item was being edited, and
    // the next click on that same code would change nothing.
    fireEvent(heading.closest('dialog'), new Event('cancel', { cancelable: true }))

    await waitFor(() => expect(document.querySelector('dialog')).toBeNull())
  })
})
