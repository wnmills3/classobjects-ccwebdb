import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listUnattachedImages: vi.fn(),
    searchInventory: vi.fn(),
    attachImage: vi.fn(),
  },
}))

import { api } from '../api'
import { renderWithProviders } from '../../test/helpers'
import Photos from './Photos'

const unattached = (overrides) => ({
  link_id: null,
  inventory_item_id: null,
  item_code: null,
  image_id: 1,
  image_role: null,
  is_primary: false,
  sort_order: 0,
  captured_at: '2026-09-01T00:00:00Z',
  thumbnail_url: '/thumb/1',
  image_url: '/img/1',
  ...overrides,
})

const foundItem = (overrides) => ({
  id: 42,
  item_code: 'C-100',
  description: '1950 Lincoln cent',
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Photos', () => {
  it("renders the server's order and does not re-sort into id order", async () => {
    // Deliberately given with a higher image_id captured *earlier*: sorting
    // by id would put /thumb/5 first, which is the opposite of what the
    // server (newest capture first) already returned. Only a page that
    // trusts the server's order renders /thumb/9 first here.
    api.listUnattachedImages.mockResolvedValue([
      unattached({
        image_id: 9,
        thumbnail_url: '/thumb/9',
        captured_at: '2026-09-18T00:00:00Z',
      }),
      unattached({
        image_id: 5,
        thumbnail_url: '/thumb/5',
        captured_at: '2026-09-01T00:00:00Z',
      }),
    ])

    renderWithProviders(<Photos />)

    const images = await screen.findAllByRole('img')
    expect(images).toHaveLength(2)
    expect(images[0]).toHaveAttribute('src', '/thumb/9')
    expect(images[1]).toHaveAttribute('src', '/thumb/5')
  })

  it('choosing an item and confirming calls api.attachImage with that item id', async () => {
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([unattached({ image_id: 7 })])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockResolvedValue({})

    renderWithProviders(<Photos />)

    const row = (await screen.findAllByRole('listitem'))[0]
    await user.type(within(row).getByLabelText('Item code'), 'C-100')
    await user.click(within(row).getByRole('button', { name: 'Find' }))

    const match = await within(row).findByRole('button', { name: /C-100/ })
    await user.click(match)

    await user.click(within(row).getByRole('button', { name: 'Link' }))

    expect(api.attachImage).toHaveBeenCalledWith(7, {
      inventoryItemId: 42,
      acknowledgeForSale: false,
    })
  })

  it('the acknowledgement checkbox is per row, not a single shared control', async () => {
    // Two rows so a leak is visible: row 7's checkbox is ticked and row 9's
    // is left alone. If the checkbox were backed by one shared piece of
    // state instead of state that lives inside each PhotoRow, ticking row
    // 7's box would silently acknowledge for row 9 too, and row 9's call
    // would carry acknowledgeForSale: true even though nobody touched its
    // control. Both calls assert the *full* argument object, not a partial
    // match, so a wrong item id or a dropped field fails here too.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([
      unattached({ image_id: 7, thumbnail_url: '/thumb/7' }),
      unattached({ image_id: 9, thumbnail_url: '/thumb/9' }),
    ])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockResolvedValue({})

    renderWithProviders(<Photos />)

    const rows = await screen.findAllByRole('listitem')
    const row7 = rows.find(
      (r) => within(r).getByRole('img').getAttribute('src') === '/thumb/7',
    )
    const row9 = rows.find(
      (r) => within(r).getByRole('img').getAttribute('src') === '/thumb/9',
    )

    // Row 7: tick the acknowledgement, then pick an item and link.
    await user.click(within(row7).getByRole('checkbox', { name: /for sale/i }))
    await user.type(within(row7).getByLabelText('Item code'), 'C-100')
    await user.click(within(row7).getByRole('button', { name: 'Find' }))
    await user.click(await within(row7).findByRole('button', { name: /C-100/ }))
    await user.click(within(row7).getByRole('button', { name: 'Link' }))

    expect(api.attachImage).toHaveBeenCalledWith(7, {
      inventoryItemId: 42,
      acknowledgeForSale: true,
    })

    // Row 9: never touched, so its checkbox stays at the default. Linking
    // it must send acknowledgeForSale: false -- proving row 7's tick did
    // not leak into row 9's call.
    await user.type(within(row9).getByLabelText('Item code'), 'C-100')
    await user.click(within(row9).getByRole('button', { name: 'Find' }))
    await user.click(await within(row9).findByRole('button', { name: /C-100/ }))
    await user.click(within(row9).getByRole('button', { name: 'Link' }))

    expect(api.attachImage).toHaveBeenCalledWith(9, {
      inventoryItemId: 42,
      acknowledgeForSale: false,
    })
  })

  it('a linked photograph leaves the list, and an untouched one stays', async () => {
    // Two rows, not one -- linking the only row would leave the list empty
    // whether or not the row-removal code does anything at all. The second
    // row proves the removal is targeted rather than incidental.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([
      unattached({ image_id: 7, thumbnail_url: '/thumb/7' }),
      unattached({ image_id: 9, thumbnail_url: '/thumb/9' }),
    ])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockResolvedValue({})

    renderWithProviders(<Photos />)

    const rows = await screen.findAllByRole('listitem')
    const targetRow = rows.find(
      (r) => within(r).getByRole('img').getAttribute('src') === '/thumb/7',
    )
    await user.type(within(targetRow).getByLabelText('Item code'), 'C-100')
    await user.click(within(targetRow).getByRole('button', { name: 'Find' }))
    const match = await within(targetRow).findByRole('button', { name: /C-100/ })
    await user.click(match)
    await user.click(within(targetRow).getByRole('button', { name: 'Link' }))

    await waitFor(() => expect(screen.getAllByRole('img')).toHaveLength(1))
    expect(screen.getByRole('img')).toHaveAttribute('src', '/thumb/9')
  })
})
