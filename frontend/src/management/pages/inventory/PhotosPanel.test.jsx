import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listItemImages: vi.fn(),
    uploadImage: vi.fn(),
    addImageFromUrl: vi.fn(),
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

    renderWithProviders(<PhotosPanel itemId={12} />, {
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

  it('holds a chosen file for the editor to save, as the obverse of a bare item', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<PhotosPanel itemId={12} onAdd={onAdd} />, {
      reference: roles,
    })
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    await user.upload(await screen.findByLabelText('Photo'), file)

    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'obverse' })
    expect(api.uploadImage).not.toHaveBeenCalled()
  })

  it('holds "Make primary" for the editor to save', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 5, image_id: 30, image_role: 'obverse' }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await user.click(await screen.findByRole('button', { name: /make primary/i }))

    expect(onEditsChange).toHaveBeenCalledWith({ 5: { is_primary: true } })
    expect(api.updateImageLink).not.toHaveBeenCalled()
  })

  it('holds a role chosen from the picker', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 6, image_id: 35, image_role: null }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'image_role' }),
      'obverse',
    )

    expect(onEditsChange).toHaveBeenCalledWith({ 6: { image_role: 'obverse' } })
    expect(api.updateImageLink).not.toHaveBeenCalled()
  })

  it('holds a cleared role as null, not an empty string', async () => {
    // null clears the role; "" is a different request to the API.
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 8, image_id: 36, image_role: 'obverse' }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'image_role' }),
      '',
    )

    expect(onEditsChange).toHaveBeenCalledWith({ 8: { image_role: null } })
  })

  it('drops a held role once the saved one is chosen again', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 8, image_id: 36, image_role: 'obverse' }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 8: { image_role: 'reverse' } }}
        onEditsChange={onEditsChange}
      />,
      { reference: roles },
    )
    const picker = await screen.findByRole('combobox', { name: 'image_role' })
    expect(picker).toHaveValue('reverse')
    expect(screen.getByText(/not saved yet/i)).toBeInTheDocument()

    await user.selectOptions(picker, 'obverse')

    expect(onEditsChange).toHaveBeenCalledWith({})
  })

  it('shows a held primary in place of the saved one, and one primary only', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 1, image_id: 10, image_role: 'obverse', is_primary: true }),
      link({ link_id: 2, image_id: 20, image_role: 'reverse', sort_order: 1 }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 2: { is_primary: true } }}
        onEditsChange={onEditsChange}
      />,
      { reference: roles },
    )
    const items = await screen.findAllByRole('listitem')
    expect(within(items[0]).queryByText('Primary')).toBeNull()
    expect(within(items[1]).getByText('Primary')).toBeVisible()

    // Back to the saved primary: the held one is simply dropped.
    await user.click(within(items[0]).getByRole('button', { name: /make primary/i }))
    expect(onEditsChange).toHaveBeenCalledWith({})
  })

  it('holds "Remove", and never deletes the photograph', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 7, image_id: 40, image_role: 'reverse', is_primary: true }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await user.click(await screen.findByRole('button', { name: /remove/i }))

    expect(onEditsChange).toHaveBeenCalledWith({ 7: { remove: true } })
    expect(api.detachImage).not.toHaveBeenCalled()
    expect(api.deleteImage).not.toHaveBeenCalled()
  })

  it('shows a held removal, with Undo and why it failed if it did', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 7, image_id: 40, image_role: 'reverse' }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 7: { remove: true, error: 'Link not found' } }}
        onEditsChange={onEditsChange}
      />,
      { reference: roles },
    )
    expect(await screen.findByText(/removed when you save/i)).toBeInTheDocument()
    expect(screen.getByText('Link not found')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /make primary/i })).toBeNull()

    await user.click(screen.getByRole('button', { name: /^undo/i }))
    expect(onEditsChange).toHaveBeenCalledWith({})
  })

  it('counts neither a held removal nor a held role when choosing the next side', async () => {
    api.listItemImages.mockResolvedValue([
      link({ link_id: 1, image_id: 10, image_role: 'obverse' }),
      link({ link_id: 2, image_id: 20, image_role: 'reverse', sort_order: 1 }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 1: { remove: true }, 2: { image_role: 'obverse' } }}
        onEditsChange={vi.fn()}
      />,
      { reference: roles },
    )
    // What stays is one obverse (re-roled), so the next is the reverse.
    expect(await screen.findByLabelText('What it shows')).toHaveValue('reverse')
  })
})

describe('PhotosPanel, an item that is for sale', () => {
  it("asks nothing itself: the editor's one acknowledgement covers its Save", async () => {
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<PhotosPanel itemId={12} />, { reference: roles })
    await screen.findByLabelText('Photo')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Change the photographs anyway')).toBeNull()
  })
})

describe('PhotosPanel: adding another photograph', () => {
  const EBAY = 'https://i.ebayimg.com/images/g/56AAAeSwlOZqboJk/s-l1600.webp'

  function render(props = {}) {
    renderWithProviders(
      <PhotosPanel
        itemId={12}

        onAdd={vi.fn()}
        onDiscard={vi.fn()}
        {...props}
      />,
      { reference: roles },
    )
  }

  function withOne(props) {
    api.listItemImages.mockResolvedValue([
      link({
        link_id: 9,
        image_id: 50,
        image_role: 'obverse',
        is_primary: true,
        sort_order: 1,
      }),
    ])
    render(props)
  }

  it('asks what a photograph shows once both sides are there', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({
        link_id: 9,
        image_id: 50,
        image_role: 'obverse',
        is_primary: true,
        sort_order: 1,
      }),
      link({ link_id: 10, image_id: 51, image_role: 'reverse', sort_order: 2 }),
    ])
    render({ onAdd })
    const what = await screen.findByRole('combobox', { name: /what it shows/i })
    expect(what).toHaveValue('')
    expect(screen.getByLabelText('Photo')).toBeDisabled()
    expect(screen.getByRole('button', { name: /add from web address/i })).toBeDisabled()
    expect(screen.getByText(/choose what the photograph shows/i)).toBeInTheDocument()

    await user.selectOptions(what, 'obverse')
    const file = new File(['x'], 'another.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Photo'), file)

    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'obverse' })
  })

  it('offers the reverse for an item with only its obverse, and holds the address', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    withOne({ onAdd })
    expect(await screen.findByRole('combobox', { name: /what it shows/i })).toHaveValue(
      'reverse',
    )
    const address = screen.getByRole('textbox', { name: /photo web address/i })
    await user.type(address, EBAY)
    await user.click(screen.getByRole('button', { name: /add from web address/i }))

    expect(onAdd).toHaveBeenCalledWith({ kind: 'url', url: EBAY, role: 'reverse' })
    expect(api.addImageFromUrl).not.toHaveBeenCalled()
    expect(address).toHaveValue('')
  })

  it('takes a pasted address as a typed one', async () => {
    const user = userEvent.setup()
    withOne()
    const address = await screen.findByRole('textbox', { name: /photo web address/i })
    address.focus()
    await user.paste(EBAY)
    expect(address).toHaveValue(EBAY)
    expect(screen.getByRole('button', { name: /add from web address/i })).toBeEnabled()
  })

  it('lists a held photograph as not saved yet, and discards it', async () => {
    const user = userEvent.setup()
    const onDiscard = vi.fn()
    withOne({
      onDiscard,
      pending: [{ key: 'p1', kind: 'url', url: EBAY, role: 'reverse' }],
    })
    const held = await screen.findByText(/not saved yet/i)
    expect(held).toHaveTextContent('Reverse')

    await user.click(screen.getByRole('button', { name: /discard/i }))
    expect(onDiscard).toHaveBeenCalledWith('p1')
  })

  it('counts a held photograph when choosing the next side', async () => {
    api.listItemImages.mockResolvedValue([])
    render({ pending: [{ key: 'p1', kind: 'url', url: EBAY, role: 'obverse' }] })
    expect(await screen.findByRole('combobox', { name: /what it shows/i })).toHaveValue(
      'reverse',
    )
  })

  it('shows a held photograph that failed to save with its reason', async () => {
    withOne({
      pending: [
        {
          key: 'p1',
          kind: 'url',
          url: EBAY,
          role: 'reverse',
          error: 'the address answered 404',
        },
      ],
    })
    expect(await screen.findByText(/the address answered 404/)).toBeInTheDocument()
  })
})
