import userEvent from '@testing-library/user-event'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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

// Scoped to the dialog: the filter panel behind it has text boxes of its own,
// and an unscoped query types into a search filter instead of the form.
async function typeDescription(user, text) {
  const dialog = document.querySelector('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: /Description/ }), text)
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

  it('closes after a save, and refreshes the results', async () => {
    const user = userEvent.setup()
    api.updateInventoryItem.mockResolvedValue({})
    await openItem(user)
    const searches = api.searchInventory.mock.calls.length

    await typeDescription(user, 'Star note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(document.querySelector('dialog')).toBeNull())
    expect(api.updateInventoryItem).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ description: 'Star note' }),
    )
    await waitFor(() =>
      expect(api.searchInventory.mock.calls.length).toBeGreaterThan(searches),
    )
  })

  it('stays open when a save is refused, so the reason can be read', async () => {
    const user = userEvent.setup()
    api.updateInventoryItem.mockRejectedValue(new Error('Changed by someone else'))
    await openItem(user)

    await typeDescription(user, 'Star note')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Changed by someone else')).toBeInTheDocument()
    expect(document.querySelector('dialog')).toHaveAttribute('open')
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
