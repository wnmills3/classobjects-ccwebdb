import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    getPurchaseOrder: vi.fn(),
    listStorageLocations: vi.fn(),
    receiveItems: vi.fn(),
    //: One line at a time means `ReceiptPanel` always has exactly one item,
    //: so it always looks the kind up -- that is what decides whether the
    //: Friedberg lookup is offered.
    getInventoryItem: vi.fn(),
    uploadImage: vi.fn(),
    searchInventory: vi.fn(),
    getItemErrors: vi.fn(),
    setItemErrors: vi.fn(),
    listItemImages: vi.fn(),
  },
}))

import { api } from '../api'
import Receiving from './Receiving'
import { adminAuth, renderWithProviders } from '../../test/helpers'

/** A button that navigates like the browser's own Back/Forward would --
 * changing the route without going through any handler of `Receiving`'s. */
function NavigateButton({ to }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(to)}>
      go
    </button>
  )
}

const MORGAN = {
  id: 412,
  item_code: 'CC-000412',
  description: '1881-S Morgan $1',
  order_number: '27-1234',
  vendor: 'eBay',
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listStorageLocations.mockResolvedValue([
    { id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' },
  ])
  api.getInventoryItem.mockResolvedValue({ id: 412, item_kind: 'coin' })
  api.getItemErrors.mockResolvedValue({ inventory_item_id: 412, errors: [] })
  api.listItemImages.mockResolvedValue([])
  api.getPurchaseOrder.mockResolvedValue({
    id: 1,
    order_number: '27-1234',
    vendor: 'eBay',
    ordered_on: '2026-08-30',
    lines: [],
  })
  api.searchInventory.mockImplementation((view, params) =>
    Promise.resolve(
      view === 'coins' && params.status === 'ordered'
        ? { rows: [MORGAN], total: 1 }
        : { rows: [], total: 0 },
    ),
  )
})

/** Opens Receiving on order 1, as the inventory's Order column links here. */
function renderOnOrder(extra = null) {
  return renderWithProviders(
    <>
      <Receiving />
      {extra}
    </>,
    { auth: adminAuth(), route: '/receiving?order=1' },
  )
}

const morganButton = () => screen.findByRole('button', { name: /CC-000412/ })

describe('Receiving', () => {
  it('is one search form: no "By order" or "By item" to choose between', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth(), route: '/receiving' })
    expect(await screen.findByLabelText(/order number/i)).toHaveValue('')
    expect(screen.queryByRole('radio', { name: /by order/i })).toBeNull()
    expect(screen.queryByRole('radio', { name: /by item/i })).toBeNull()
    // Nothing is searched until asked.
    expect(api.searchInventory).not.toHaveBeenCalled()
  })

  it('opens a linked order already searched, with its header', async () => {
    api.getPurchaseOrder.mockResolvedValue({
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      source_url: 'https://www.ebay.com/itm/1',
      lines: [],
    })
    renderOnOrder()
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(1))

    expect(await morganButton()).toBeInTheDocument()
    expect(screen.getByLabelText(/order number/i)).toHaveValue('27-1234')
    expect(
      api.searchInventory.mock.calls.every(([, p]) => p.purchase_order_id === 1),
    ).toBe(true)
    const link = screen.getByRole('link', { name: 'Vendor page' })
    expect(link).toHaveAttribute('href', 'https://www.ebay.com/itm/1')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows no vendor link when the order carries no source url', async () => {
    renderOnOrder()
    await morganButton()
    expect(screen.queryByRole('link', { name: 'Vendor page' })).toBeNull()
  })

  it('drops a failed link’s error once the address names another order', async () => {
    api.getPurchaseOrder.mockImplementation((id) =>
      id === 1
        ? Promise.reject(new Error('Purchase order not found'))
        : Promise.resolve({
            id: 2,
            order_number: '27-9999',
            vendor: 'eBay',
            ordered_on: '2026-08-30',
            lines: [],
          }),
    )
    renderOnOrder(<NavigateButton to="/receiving?order=2" />)
    expect(await screen.findByText('Purchase order not found')).toBeInTheDocument()

    screen.getByText('go').click()
    expect(await screen.findByRole('heading', { name: /27-9999/ })).toBeInTheDocument()
    expect(screen.queryByText('Purchase order not found')).toBeNull()
  })

  it('follows the route to another order, as Back/Forward would', async () => {
    renderOnOrder(<NavigateButton to="/receiving?order=43" />)
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(1))
    screen.getByText('go').click()
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(43))
  })
})

describe('receiving one line at a time', () => {
  it('opens the picked line in a dialog that names it', async () => {
    const user = userEvent.setup()
    renderOnOrder()
    await user.click(await morganButton())

    const dialog = await screen.findByRole('dialog')
    // Named, so a mis-click is obvious before a location is typed into it.
    expect(dialog).toHaveAccessibleName(/CC-000412/)
    expect(within(dialog).getByText('1881-S Morgan $1')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/arrived/i)).toBeInTheDocument()
  })

  it('records the receipt for that one line only', async () => {
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderOnOrder()
    await user.click(await morganButton())
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    await waitFor(() =>
      expect(api.receiveItems).toHaveBeenCalledWith(
        expect.objectContaining({ item_ids: [412] }),
      ),
    )
  })

  it('closes the dialog and searches again once the receipt lands', async () => {
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderOnOrder()
    await user.click(await morganButton())
    const searchesBefore = api.searchInventory.mock.calls.length
    const dialog = await screen.findByRole('dialog')

    // What arrived is no longer "not yet arrived": the repeated search
    // must not offer it again.
    api.searchInventory.mockResolvedValue({ rows: [], total: 0 })
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() =>
      expect(api.searchInventory.mock.calls.length).toBeGreaterThan(searchesBefore),
    )
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /CC-000412/ })).toBeNull(),
    )
  })

  it('closes without recording anything when Close is clicked', async () => {
    const user = userEvent.setup()
    renderOnOrder()
    await user.click(await morganButton())
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.receiveItems).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and says so when a photograph fails to upload', async () => {
    // The receipt is recorded; the photograph is not. Closing on `onDone`
    // would report the failure to a component nobody can see.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    api.uploadImage.mockRejectedValue(new Error('file too large'))
    renderOnOrder()
    await user.click(await morganButton())
    const dialog = await screen.findByRole('dialog')
    await user.upload(
      within(dialog).getByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    await waitFor(() => expect(api.uploadImage).toHaveBeenCalled())
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(await screen.findByText(/obverse\.jpg.*file too large/i)).toBeInTheDocument()

    // The receipt was recorded, so closing must search again: the item is
    // no longer "not yet arrived" (code review, 2026-09-23).
    const before = api.searchInventory.mock.calls.length
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }),
    )
    await waitFor(() =>
      expect(api.searchInventory.mock.calls.length).toBeGreaterThan(before),
    )
  })

  it('offers the previous line’s location again on the next one', async () => {
    // A parcel of twenty into one safe deposit box must not be twenty
    // identical dropdown picks.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderOnOrder()
    await user.click(await morganButton())
    let dialog = await screen.findByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText(/storage location/i), '3')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(await morganButton())
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/storage location/i)).toHaveValue('3')
  })

  it('does not carry a note from one line to the next', async () => {
    // A location describes the parcel; a note describes the object.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderOnOrder()
    await user.click(await morganButton())
    let dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(/note/i), 'corner bent')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(await morganButton())
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/note/i)).toHaveValue('')
  })
})

describe('an open dialog belongs to the order it was opened from', () => {
  it('closes when the route moves to another order, as Back/Forward would', async () => {
    // Back/Forward goes through no handler here, and a modal does not block
    // browser chrome: a dialog left open would submit against a search no
    // longer on screen.
    const user = userEvent.setup()
    renderOnOrder(<NavigateButton to="/receiving?order=2" />)
    await user.click(await morganButton())
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByText('go'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
