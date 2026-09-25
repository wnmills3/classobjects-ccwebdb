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

// Mirrors `ApiError` well enough for these tests: `PhotoRow` only ever
// reads `.status` and `.message`, never `instanceof`s the real class.
function forSaleError(message = 'For sale -- listing #3 at 120.00.') {
  const err = new Error(message)
  err.status = 409
  return err
}

// A row's search-and-pick sequence, factored out because the refusal tests
// below all need it at least twice -- once before the refusal and once
// after -- and repeating it inline would bury which row is being acted on
// under boilerplate identical across every call site.
async function pickItem(user, row) {
  await user.type(within(row).getByLabelText('Item code'), 'C-100')
  await user.click(within(row).getByRole('button', { name: 'Find' }))
  await user.click(await within(row).findByRole('button', { name: /C-100/ }))
}

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

  it('a refused attach shows the server refusal for that row and keeps it listed', async () => {
    // The first attempt never acknowledges -- see the module docstring: the
    // question is asked only once the server has actually said there is
    // one to ask. `mockRejectedValueOnce` enforces that there is exactly
    // one call, and it carries acknowledgeForSale: false.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([unattached({ image_id: 7 })])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    const message = 'For sale -- listing #3 at 120.00.'
    api.attachImage.mockRejectedValueOnce(forSaleError(message))

    renderWithProviders(<Photos />)

    const row = (await screen.findAllByRole('listitem'))[0]
    await pickItem(user, row)
    await user.click(within(row).getByRole('button', { name: 'Link' }))

    expect(await within(row).findByText(message)).toBeVisible()
    expect(api.attachImage).toHaveBeenCalledTimes(1)
    expect(api.attachImage).toHaveBeenCalledWith(7, {
      inventoryItemId: 42,
      acknowledgeForSale: false,
    })
    // `error` and `refusal` render the same verbatim text through different
    // elements, so the message alone does not say which branch produced it.
    // The refusal block's "Link anyway" control is the ordinary-error branch's
    // one visible difference -- its presence is what proves this came from
    // the refusal path rather than the plain error path.
    expect(
      within(row).getByRole('button', { name: /link anyway/i }),
    ).toBeInTheDocument()
    // A refusal is a question, not a removal -- the row is still here to
    // answer it.
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('confirming the refusal re-attempts with acknowledgeForSale: true, and the row then leaves', async () => {
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([unattached({ image_id: 7 })])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockRejectedValueOnce(forSaleError()).mockResolvedValueOnce({})

    renderWithProviders(<Photos />)

    const row = (await screen.findAllByRole('listitem'))[0]
    await pickItem(user, row)
    await user.click(within(row).getByRole('button', { name: 'Link' }))
    await within(row).findByText(/for sale/i)

    await user.click(within(row).getByRole('button', { name: /link anyway/i }))

    expect(api.attachImage).toHaveBeenCalledTimes(2)
    expect(api.attachImage).toHaveBeenNthCalledWith(2, 7, {
      inventoryItemId: 42,
      acknowledgeForSale: true,
    })
    await waitFor(() => expect(screen.queryAllByRole('listitem')).toHaveLength(0))
  })

  it('a refusal on one row leaves a second row untouched -- no refusal shown, no acknowledgement carried', async () => {
    // Two rows, the same discipline the row-leaves test below uses: row 7
    // is refused and answered; row 9 is never clicked into a refusal at
    // all. If the refusal (or the acknowledgement it unlocks) were held in
    // state shared across rows rather than inside each PhotoRow, row 9
    // would either show row 7's refusal text or send
    // acknowledgeForSale: true despite never having been told to.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([
      unattached({ image_id: 7, thumbnail_url: '/thumb/7' }),
      unattached({ image_id: 9, thumbnail_url: '/thumb/9' }),
    ])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockRejectedValueOnce(forSaleError()).mockResolvedValueOnce({})

    renderWithProviders(<Photos />)

    const rows = await screen.findAllByRole('listitem')
    const row7 = rows.find(
      (r) => within(r).getByRole('img').getAttribute('src') === '/thumb/7',
    )
    const row9 = rows.find(
      (r) => within(r).getByRole('img').getAttribute('src') === '/thumb/9',
    )

    await pickItem(user, row7)
    await user.click(within(row7).getByRole('button', { name: 'Link' }))
    await within(row7).findByText(/for sale/i)

    expect(within(row9).queryByRole('alert')).toBeNull()
    expect(within(row9).queryByRole('button', { name: /link anyway/i })).toBeNull()

    await pickItem(user, row9)
    await user.click(within(row9).getByRole('button', { name: 'Link' }))

    expect(api.attachImage).toHaveBeenCalledWith(9, {
      inventoryItemId: 42,
      acknowledgeForSale: false,
    })
  })

  it('a non-"For sale" failure shows an ordinary error and keeps the row', async () => {
    // Not a 409, and not the "For sale" prefix `sale_state.guard` always
    // uses -- this must take the plain error branch, never the refusal one.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([unattached({ image_id: 7 })])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    api.attachImage.mockRejectedValueOnce(new Error('Network error'))

    renderWithProviders(<Photos />)

    const row = (await screen.findAllByRole('listitem'))[0]
    await pickItem(user, row)
    await user.click(within(row).getByRole('button', { name: 'Link' }))

    expect(await within(row).findByText('Network error')).toBeVisible()
    // No refusal UI for an ordinary failure -- nothing here to "confirm".
    expect(within(row).queryByRole('button', { name: /link anyway/i })).toBeNull()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('a 409 that is not a for-sale refusal shows an ordinary error', async () => {
    // The untested half of a double condition. The test above carries no
    // status at all, so `err.status === 409` alone was never the thing being
    // exercised -- if the guard ever degrades to the status check, this is
    // what catches it. `attach_image` has its own 409 ("already has this
    // photograph"), and rendering that as a for-sale refusal would offer a
    // "Link anyway" button that retries the same conflict forever.
    const user = userEvent.setup()
    api.listUnattachedImages.mockResolvedValue([unattached({ image_id: 7 })])
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ view, rows: view === 'coins' ? [foundItem()] : [] }),
    )
    const conflict = new Error('C-100 already has this photograph (link #4).')
    conflict.status = 409
    api.attachImage.mockRejectedValueOnce(conflict)

    renderWithProviders(<Photos />)

    const row = (await screen.findAllByRole('listitem'))[0]
    await pickItem(user, row)
    await user.click(within(row).getByRole('button', { name: 'Link' }))

    expect(await within(row).findByText(conflict.message)).toBeVisible()
    expect(within(row).queryByRole('button', { name: /link anyway/i })).toBeNull()
    // The plain error branch keeps the Link button; the refusal branch
    // replaces it. Its presence is what says which branch ran.
    expect(within(row).getByRole('button', { name: 'Link' })).toBeInTheDocument()
    expect(api.attachImage).toHaveBeenCalledTimes(1)
  })

  it('a failed load shows the error and does not also claim there is nothing to do', async () => {
    // The catch sets rows to [] so the page stays usable rather than stuck
    // on "Loading..." -- which without the `!error` guard renders the
    // failure and the empty state together, telling the operator both that
    // the page failed and that there is nothing waiting.
    api.listUnattachedImages.mockRejectedValue(new Error('Service unavailable'))

    renderWithProviders(<Photos />)

    expect(await screen.findByText('Service unavailable')).toBeVisible()
    expect(screen.queryByText('Nothing waiting to be filed.')).toBeNull()
    expect(screen.queryByText('Loading...')).toBeNull()
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
