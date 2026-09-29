import { useState } from 'react'

import userEvent from '@testing-library/user-event'
import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listItemImages: vi.fn(),
    uploadImage: vi.fn(),
    addImageFromUrl: vi.fn(),
    updateImageLink: vi.fn(),
    detachImage: vi.fn(),
    moveImageLink: vi.fn(),
    // The item picker's lookup, for Move.
    searchInventory: vi.fn(),
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
    const user = userEvent.setup()
    const onAdd = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 1, image_id: 10, image_role: 'obverse' }),
      link({ link_id: 2, image_id: 20, image_role: 'reverse', sort_order: 1 }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 1: { remove: true }, 2: { image_role: 'obverse' } }}
        onEditsChange={vi.fn()}
        onAdd={onAdd}
      />,
      { reference: roles },
    )
    const file = new File(['x'], 'next.jpg', { type: 'image/jpeg' })
    await user.upload(await screen.findByLabelText('Photo'), file)
    // What stays is one obverse (re-roled), so the next is the reverse.
    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'reverse' })
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

  it('holds a photograph with no role once both sides are there', async () => {
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
    // No picker beside the add controls: a role belongs to a photograph.
    expect(await screen.findByLabelText('Photo')).toBeEnabled()
    expect(screen.queryByRole('combobox', { name: /what it shows/i })).toBeNull()

    const file = new File(['x'], 'another.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Photo'), file)

    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: '' })
  })

  it('gives each held photograph its own picker for what it shows', async () => {
    const user = userEvent.setup()
    const onRoleChange = vi.fn()
    api.listItemImages.mockResolvedValue([])
    render({
      onRoleChange,
      pending: [
        { key: 'p1', kind: 'url', url: EBAY, role: 'obverse' },
        { key: 'p2', kind: 'url', url: `${EBAY}?2`, role: '' },
      ],
    })
    const first = await screen.findByRole('combobox', { name: `What ${EBAY} shows` })
    const second = screen.getByRole('combobox', { name: `What ${EBAY}?2 shows` })
    expect(first).toHaveValue('obverse')
    expect(second).toHaveValue('')
    expect(screen.getByText(/choose what it shows before saving/i)).toBeInTheDocument()

    await user.selectOptions(second, 'reverse')
    expect(onRoleChange).toHaveBeenCalledWith('p2', 'reverse')
  })

  it('offers the reverse for an item with only its obverse, and holds the address', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    withOne({ onAdd })
    const address = await screen.findByRole('textbox', { name: /photo web address/i })
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
    expect(await screen.findByText(/not saved yet/i)).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: `What ${EBAY} shows` })).toHaveValue(
      'reverse',
    )

    await user.click(screen.getByRole('button', { name: /discard/i }))
    expect(onDiscard).toHaveBeenCalledWith('p1')
  })

  it('counts a held photograph when choosing the next side', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    api.listItemImages.mockResolvedValue([])
    render({ onAdd, pending: [{ key: 'p1', kind: 'url', url: EBAY, role: 'obverse' }] })
    const file = new File(['x'], 'back.jpg', { type: 'image/jpeg' })
    await user.upload(await screen.findByLabelText('Photo'), file)
    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'reverse' })
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

describe('PhotosPanel: moving a photograph to another item', () => {
  const OTHER = { id: 99, item_code: 'CC-000099', description: 'Another note' }

  function found(row) {
    api.searchInventory.mockImplementation((view) =>
      Promise.resolve({ rows: view === 'currency' ? [row] : [], total: 1 }),
    )
  }

  async function pick(user, code) {
    await user.click(await screen.findByRole('button', { name: 'Move (photo 30)' }))
    await user.type(screen.getByRole('textbox', { name: 'Item code' }), code)
    await user.click(screen.getByRole('button', { name: 'Find' }))
    await user.click(await screen.findByRole('button', { name: new RegExp(code) }))
  }

  it('holds a move to the item found by its code', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    found(OTHER)
    api.listItemImages.mockResolvedValue([
      link({ link_id: 5, image_id: 30, image_role: 'obverse', is_primary: true }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await pick(user, 'CC-000099')

    expect(onEditsChange).toHaveBeenCalledWith({
      5: { move_to: { id: 99, item_code: 'CC-000099' } },
    })
    expect(api.moveImageLink).not.toHaveBeenCalled()
  })

  it('refuses the item the photograph is already on', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    found({ id: 12, item_code: 'C-012', description: 'This one' })
    api.listItemImages.mockResolvedValue([
      link({ link_id: 5, image_id: 30, image_role: 'obverse', is_primary: true }),
    ])
    renderWithProviders(<PhotosPanel itemId={12} onEditsChange={onEditsChange} />, {
      reference: roles,
    })

    await pick(user, 'C-012')

    expect(screen.getByText(/already on this item/i)).toBeInTheDocument()
    expect(onEditsChange).not.toHaveBeenCalled()
  })

  it('shows a held move with Undo, and counts it out of the next side', async () => {
    const user = userEvent.setup()
    const onEditsChange = vi.fn()
    const onAdd = vi.fn()
    api.listItemImages.mockResolvedValue([
      link({ link_id: 5, image_id: 30, image_role: 'obverse', is_primary: true }),
    ])
    renderWithProviders(
      <PhotosPanel
        itemId={12}
        edits={{ 5: { move_to: { id: 99, item_code: 'CC-000099' } } }}
        onEditsChange={onEditsChange}
        onAdd={onAdd}
      />,
      { reference: roles },
    )
    expect(
      await screen.findByText(/moves to CC-000099 when you save/i),
    ).toBeInTheDocument()
    expect(screen.queryByText('Primary')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Move (photo 30)' })).toBeNull()

    // The obverse is leaving, so a photograph added now is the obverse.
    const file = new File(['x'], 'front.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Photo'), file)
    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'obverse' })

    await user.click(screen.getByRole('button', { name: 'Undo (photo 30)' }))
    expect(onEditsChange).toHaveBeenCalledWith({})
  })
})

describe('PhotosPanel: drag-and-drop and paste', () => {
  // jsdom has no DataTransfer constructor, so tests build the minimal shape
  // the handlers actually read: files/items/types, matching a real drag's.
  function dataTransferFor(file) {
    return {
      files: [file],
      items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
      types: ['Files'],
    }
  }

  async function renderPanel(props = {}) {
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<PhotosPanel itemId={12} {...props} />, { reference: roles })
    return screen.findByLabelText(/drag.*paste/i)
  }

  it('adds a dropped image file the same way as choosing one', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    fireEvent.drop(zone, { dataTransfer: dataTransferFor(file) })

    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'obverse' })
  })

  it('shows the drop hint on dragenter/dragover, and clears it on dragleave', async () => {
    const zone = await renderPanel()
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    fireEvent.dragEnter(zone, { dataTransfer: dataTransferFor(file) })
    expect(zone).toHaveClass('photo-drop-active')

    fireEvent.dragLeave(zone, { dataTransfer: dataTransferFor(file) })
    expect(zone).not.toHaveClass('photo-drop-active')
  })

  it('clears the drop hint once the file lands', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    fireEvent.dragEnter(zone, { dataTransfer: dataTransferFor(file) })
    fireEvent.drop(zone, { dataTransfer: dataTransferFor(file) })

    expect(zone).not.toHaveClass('photo-drop-active')
  })

  it('refuses a dropped non-image file with a readable message', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' })

    fireEvent.drop(zone, { dataTransfer: dataTransferFor(file) })

    expect(await screen.findByText(/notes\.txt.*not an image/i)).toBeInTheDocument()
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('adds a pasted image the same way as choosing one', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })
    const file = new File(['x'], 'coin.png', { type: 'image/png' })

    fireEvent.paste(zone, {
      clipboardData: {
        items: [{ kind: 'file', type: 'image/png', getAsFile: () => file }],
      },
    })

    expect(onAdd).toHaveBeenCalledWith({ kind: 'file', file, role: 'obverse' })
  })

  it('does nothing on a paste with no image', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })

    fireEvent.paste(zone, {
      clipboardData: {
        items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }],
      },
    })

    expect(onAdd).not.toHaveBeenCalled()
    expect(screen.queryByText(/not an image/i)).toBeNull()
  })

  it('keeps the drop hint lit while the drag moves over a child element', async () => {
    const zone = await renderPanel()
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })
    const label = within(zone).getByText('Photo')

    fireEvent.dragEnter(zone, { dataTransfer: dataTransferFor(file) })
    expect(zone).toHaveClass('photo-drop-active')

    // Moving onto a child inside the same drop target: the child's own
    // dragenter bubbles up to the zone before the zone's own dragleave
    // fires -- a bare on/off flag would blink the hint off right here.
    fireEvent.dragEnter(label, { dataTransfer: dataTransferFor(file) })
    fireEvent.dragLeave(zone, { dataTransfer: dataTransferFor(file) })

    expect(zone).toHaveClass('photo-drop-active')

    // Leaving for real (one more leave than the enters above) still clears it.
    fireEvent.dragLeave(zone, { dataTransfer: dataTransferFor(file) })
    expect(zone).not.toHaveClass('photo-drop-active')
  })

  it('clears the drop hint when the drag is cancelled outside the panel', async () => {
    const zone = await renderPanel()
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    fireEvent.dragEnter(zone, { dataTransfer: dataTransferFor(file) })
    expect(zone).toHaveClass('photo-drop-active')

    // A drag cancelled by Escape, or dropped outside the browser window
    // entirely, never sends this panel another dragleave of its own --
    // dragend on the source is the only signal left, and it lands on the
    // window rather than on the panel.
    fireEvent.dragEnd(window)

    expect(zone).not.toHaveClass('photo-drop-active')
  })

  it('adds two dropped image files, each with its own role', async () => {
    const onAdd = vi.fn()
    const zone = await renderPanel({ onAdd })
    const front = new File(['1'], 'front.jpg', { type: 'image/jpeg' })
    const back = new File(['2'], 'back.jpg', { type: 'image/jpeg' })

    fireEvent.drop(zone, {
      dataTransfer: {
        files: [front, back],
        items: [
          { kind: 'file', type: front.type, getAsFile: () => front },
          { kind: 'file', type: back.type, getAsFile: () => back },
        ],
        types: ['Files'],
      },
    })

    expect(onAdd).toHaveBeenNthCalledWith(1, {
      kind: 'file',
      file: front,
      role: 'obverse',
    })
    expect(onAdd).toHaveBeenNthCalledWith(2, {
      kind: 'file',
      file: back,
      role: 'reverse',
    })
  })
})

describe('PhotosPanel: a drop or a paste lands in the held list', () => {
  // PhotosPanel itself holds nothing -- `onAdd` reports to the editor, which
  // owns `pending` and re-renders this panel with it. This wrapper stands in
  // for that editor so a drop's or a paste's result can be read from the
  // same "not saved yet" list a chosen file already lands in.
  function Wired(props) {
    const [pending, setPending] = useState([])
    function onAdd(entry) {
      setPending((current) => [...current, { ...entry, key: `k${current.length}` }])
    }
    function onDiscard(key) {
      setPending((current) => current.filter((entry) => entry.key !== key))
    }
    return (
      <PhotosPanel
        itemId={12}
        pending={pending}
        onAdd={onAdd}
        onDiscard={onDiscard}
        {...props}
      />
    )
  }

  it('shows a dropped photograph as not saved yet, with Discard', async () => {
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<Wired />, { reference: roles })
    const zone = await screen.findByLabelText(/drag.*paste/i)
    const file = new File(['x'], 'coin.jpg', { type: 'image/jpeg' })

    fireEvent.drop(zone, {
      dataTransfer: {
        files: [file],
        items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
        types: ['Files'],
      },
    })

    expect(await screen.findByText(/coin\.jpg -- not saved yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /discard/i })).toBeInTheDocument()
  })

  it('shows a pasted photograph as not saved yet, with Discard', async () => {
    api.listItemImages.mockResolvedValue([])
    renderWithProviders(<Wired />, { reference: roles })
    const zone = await screen.findByLabelText(/drag.*paste/i)
    const file = new File(['x'], 'coin.png', { type: 'image/png' })

    fireEvent.paste(zone, {
      clipboardData: {
        items: [{ kind: 'file', type: 'image/png', getAsFile: () => file }],
      },
    })

    expect(await screen.findByText(/coin\.png -- not saved yet/i)).toBeInTheDocument()
  })
})
