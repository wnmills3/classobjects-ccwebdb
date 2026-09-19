import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listItemImages: vi.fn(),
    uploadImage: vi.fn(),
    updateImageLink: vi.fn(),
    detachImage: vi.fn(),
    // Never called by this panel -- see "Remove detaches, never deletes"
    // below. Mocked anyway so a mistaken call is a clean assertion failure
    // rather than a TypeError on an undefined function.
    deleteImage: vi.fn(),
  },
}))

import { api } from '../../api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import PhotosPanel from './PhotosPanel'

const roles = emptyReference({
  tables: {
    image_role: [
      { code: 'obverse', label: 'Obverse', source: 'seeded', extra: {} },
      { code: 'reverse', label: 'Reverse', source: 'seeded', extra: {} },
    ],
  },
})

const link = (overrides) => ({
  link_id: 1,
  inventory_item_id: 12,
  item_code: 'C-012',
  image_id: 1,
  image_role: null,
  is_primary: false,
  sort_order: 0,
  captured_at: null,
  thumbnail_url: '/thumb/1',
  image_url: '/img/1',
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('PhotosPanel', () => {
  it('lists photographs in sort_order, marking the primary one', async () => {
    // Deliberately returned out of order -- this only passes if the panel
    // sorts by sort_order itself rather than trusting the API's order.
    api.listItemImages.mockResolvedValue([
      link({
        link_id: 2,
        image_id: 20,
        image_role: 'reverse',
        is_primary: false,
        sort_order: 1,
        thumbnail_url: '/thumb/20',
      }),
      link({
        link_id: 1,
        image_id: 10,
        image_role: 'obverse',
        is_primary: true,
        sort_order: 0,
        thumbnail_url: '/thumb/10',
      }),
    ])

    renderWithProviders(<PhotosPanel itemId={12} saleState={[]} />, {
      reference: roles,
    })

    const items = await screen.findAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByRole('img')).toHaveAttribute('src', '/thumb/10')
    expect(within(items[0]).getByText('Primary')).toBeVisible()
    expect(within(items[1]).getByRole('img')).toHaveAttribute('src', '/thumb/20')
    expect(within(items[1]).queryByText('Primary')).toBeNull()
    expect(api.listItemImages).toHaveBeenCalledWith(12)
  })

  it('uploads a photograph through api.uploadImage with the item id', async () => {
    const user = userEvent.setup()
    api.listItemImages.mockResolvedValue([])
    api.uploadImage.mockResolvedValue({})
    renderWithProviders(<PhotosPanel itemId={12} saleState={[]} />, {
      reference: roles,
    })
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    await user.upload(await screen.findByLabelText('Photo'), file)

    expect(api.uploadImage).toHaveBeenCalledWith(12, file, {
      acknowledgeForSale: false,
    })
  })

  it('"Make primary" calls updateImageLink with isPrimary true', async () => {
    const user = userEvent.setup()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 5, image_id: 30, image_role: 'obverse' }),
    ])
    api.updateImageLink.mockResolvedValue({})
    renderWithProviders(<PhotosPanel itemId={12} saleState={[]} />, {
      reference: roles,
    })

    await user.click(await screen.findByRole('button', { name: /make primary/i }))

    expect(api.updateImageLink).toHaveBeenCalledWith(5, {
      isPrimary: true,
      acknowledgeForSale: false,
    })
  })

  it('"Remove" detaches the link and never deletes the photograph', async () => {
    const user = userEvent.setup()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 7, image_id: 40, image_role: 'reverse', is_primary: true }),
    ])
    api.detachImage.mockResolvedValue(null)
    renderWithProviders(<PhotosPanel itemId={12} saleState={[]} />, {
      reference: roles,
    })

    await user.click(await screen.findByRole('button', { name: /remove/i }))

    expect(api.detachImage).toHaveBeenCalledWith(7, { acknowledgeForSale: false })
    expect(api.deleteImage).not.toHaveBeenCalled()
  })
})

describe('PhotosPanel, an item that is for sale', () => {
  it('warns, then carries the acknowledgement into every later write', async () => {
    const user = userEvent.setup()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 9, image_id: 50, image_role: 'obverse', is_primary: false }),
    ])
    api.updateImageLink.mockResolvedValue({})
    api.detachImage.mockResolvedValue(null)
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        saleState={[{ kind: 'listing', id: 3, text: 'listing #3 at 120.00' }]}
      />,
      // StrictMode, the same reason ErrorsPanel's equivalent test asks for
      // it: an effect guard that survives one setup/cleanup/setup but not
      // two is invisible to an ordinary render.
      { reference: roles, strict: true },
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('listing #3 at 120.00')
    await user.click(screen.getByLabelText('Change the photographs anyway'))

    await user.click(screen.getByRole('button', { name: /make primary/i }))
    expect(api.updateImageLink).toHaveBeenLastCalledWith(9, {
      isPrimary: true,
      acknowledgeForSale: true,
    })

    // Sticky: the second write does not ask again.
    await user.click(screen.getByRole('button', { name: /remove/i }))
    expect(api.detachImage).toHaveBeenLastCalledWith(9, { acknowledgeForSale: true })
  })

  it('renders no warning for an item that is not for sale', async () => {
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<PhotosPanel itemId={12} saleState={[]} />, {
      reference: roles,
    })
    await screen.findByLabelText('Photo')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
