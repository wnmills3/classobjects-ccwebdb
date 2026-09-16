import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    listStorageLocations: vi.fn(),
    receiveItems: vi.fn(),
    //: One line at a time means `ReceiptPanel` always has exactly one item,
    //: so it always looks the kind up -- that is what decides whether the
    //: Friedberg lookup is offered. Under the old bulk panel an empty
    //: selection meant this was never called.
    getInventoryItem: vi.fn(),
    uploadImage: vi.fn(),
    searchInventory: vi.fn(),
  },
}))

import { api } from '../api'
import Receiving from './Receiving'
import { adminAuth, renderWithProviders } from '../../test/helpers'

/** Renders the current route so a test can assert the URL a click produced. */
function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

/** A button that navigates like the browser's own Back/Forward would --
 * changing the route without going through `Receiving`'s own pick handler. */
function NavigateButton({ to }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(to)}>
      go
    </button>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listPurchaseOrders.mockResolvedValue([
    {
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      outstanding: 3,
      total: 5,
    },
  ])
  api.listStorageLocations.mockResolvedValue([
    { id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' },
  ])
  api.getInventoryItem.mockResolvedValue({ id: 412, item_kind: 'coin' })
  api.getPurchaseOrder.mockResolvedValue({
    id: 1,
    order_number: '27-1234',
    vendor: 'eBay',
    ordered_on: '2026-08-30',
    lines: [
      {
        id: 412,
        item_code: 'CC-000412',
        description: '1881-S Morgan $1',
        item_cost: '84.00',
        status: 'ordered',
      },
      {
        id: 413,
        item_code: 'CC-000413',
        description: '1923 Peace $1',
        item_cost: '91.00',
        status: 'received',
      },
    ],
  })
})

describe('Receiving', () => {
  it('offers the orders that still have something outstanding', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    expect(await screen.findByText(/27-1234/)).toBeInTheDocument()
    expect(screen.getByText(/3 of 5/i)).toBeInTheDocument()
  })

  it('lists every line on the order, including one that has already arrived', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(1))
    expect(await screen.findByText('CC-000412')).toBeInTheDocument()
    // Shown for context, but not selectable -- OrderLines.test.jsx covers
    // that in detail; this only checks the two are wired together.
    expect(await screen.findByText('CC-000413')).toBeInTheDocument()
  })
})

describe('receiving one line at a time', () => {
  it('opens the picked line in a dialog that names it', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))

    const dialog = await screen.findByRole('dialog')
    // Named, so a mis-click is obvious before a location is typed into it.
    expect(dialog).toHaveAccessibleName(/CC-000412/)
    expect(within(dialog).getByText('1881-S Morgan $1')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/arrived/i)).toBeInTheDocument()
  })

  it('records the receipt for that one line only', async () => {
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    await waitFor(() =>
      expect(api.receiveItems).toHaveBeenCalledWith(
        expect.objectContaining({ item_ids: [412] }),
      ),
    )
  })

  it('closes the dialog and pulls the order again once the receipt lands', async () => {
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    // Closed: the line behind it must not still read `ordered` under a dialog
    // that has already gone, so the order is re-fetched as well.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.getPurchaseOrder).toHaveBeenCalledTimes(2)
  })

  it('closes without recording anything when Close is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.receiveItems).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and says so when a photograph fails to upload', async () => {
    // The receipt is recorded; the photograph is not. Closing on `onDone`
    // unmounts the panel, so reporting the failure after that would write it
    // to a component nobody can see -- the operator would be told the receipt
    // landed and never learn the provenance photo did not.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    api.uploadImage.mockRejectedValue(new Error('file too large'))
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    const dialog = await screen.findByRole('dialog')
    await user.upload(
      within(dialog).getByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))

    await waitFor(() => expect(api.uploadImage).toHaveBeenCalled())
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(await screen.findByText(/obverse\.jpg.*file too large/i)).toBeInTheDocument()
  })

  it('offers the previous line’s location again on the next one', async () => {
    // A parcel of twenty into one safe deposit box must not be twenty
    // identical dropdown picks.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    let dialog = await screen.findByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText(/storage location/i), '3')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'CC-000412' }))
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/storage location/i)).toHaveValue('3')
  })

  it('does not carry a note from one line to the next', async () => {
    // A location describes the parcel; a note describes the object. Repeating
    // one writes a fact about a coin nobody checked.
    const user = userEvent.setup()
    api.receiveItems.mockResolvedValue({ received: 1 })
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    let dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(/note/i), 'corner bent')
    await user.click(within(dialog).getByRole('button', { name: 'Receive' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'CC-000412' }))
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/note/i)).toHaveValue('')
  })

  it('no longer offers a bulk selection', async () => {
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })
    await screen.findByText('CC-000412')

    // The checkbox column and the select-all went with the bulk panel; the
    // two radios choosing the way in are the only inputs of that shape left.
    expect(screen.queryByLabelText(/select all/i)).not.toBeInTheDocument()
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })
})

describe('an open dialog belongs to the order it was opened from', () => {
  it('closes when the route moves to another order, as Back/Forward would', async () => {
    // Neither Back/Forward nor an `?order=` link goes through a handler here,
    // and a modal does not block browser chrome. Held as a bare line, the
    // dialog for order 1 stayed open over order 2's table and would have
    // submitted against it -- then reloaded the order on screen, so the line
    // actually received was refreshed nowhere.
    const user = userEvent.setup()
    renderWithProviders(
      <>
        <Receiving />
        <NavigateButton to="/receiving?order=2" />
      </>,
      { auth: adminAuth(), route: '/receiving?order=1' },
    )

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByText('go'))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('closes when "Choose another order" goes back to the picker', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /choose another order/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('closes when the operator switches to searching by item', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })

    await user.click(await screen.findByRole('button', { name: 'CC-000412' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: /by item/i }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('an order named in the URL scopes the page', () => {
  it('shows only that order, not the list of every other one', async () => {
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })
    await screen.findByText('CC-000412')

    // The picker's "3 of 5 outstanding" summary is how the list renders; a
    // link about one purchase must not open onto all of them.
    expect(screen.queryByText(/3 of 5/i)).not.toBeInTheDocument()
  })

  it('offers the picker again on "Choose another order"', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=1',
    })
    await screen.findByText('CC-000412')

    await user.click(screen.getByRole('button', { name: /choose another order/i }))

    expect(await screen.findByText(/3 of 5/i)).toBeInTheDocument()
    expect(screen.queryByText('CC-000412')).not.toBeInTheDocument()
  })

  it('shows the picker when no order is named', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth(), route: '/receiving' })
    expect(await screen.findByText(/3 of 5/i)).toBeInTheDocument()
  })
})

describe('opening an order from the URL', () => {
  it('opens the order named by ?order= without needing a pick', async () => {
    renderWithProviders(<Receiving />, {
      auth: adminAuth(),
      route: '/receiving?order=42',
    })
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(42))
  })

  it('follows the route when it changes after mount, as Back/Forward would', async () => {
    renderWithProviders(
      <>
        <Receiving />
        <NavigateButton to="/receiving?order=43" />
      </>,
      { auth: adminAuth(), route: '/receiving?order=42' },
    )
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(42))

    screen.getByText('go').click()

    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(43))
  })

  it('puts the picked order in the URL', async () => {
    renderWithProviders(
      <>
        <Receiving />
        <LocationProbe />
      </>,
      { auth: adminAuth(), route: '/receiving' },
    )
    const order = await screen.findByText(/27-1234/)
    order.click()
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/receiving?order=1'),
    )
  })
})

describe('the vendor page link', () => {
  it('links to the vendor page when the order source is a web address', async () => {
    api.getPurchaseOrder.mockResolvedValue({
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      source_url: 'https://www.ebay.com/itm/1',
      lines: [],
    })
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()

    const link = await screen.findByRole('link', { name: 'Vendor page' })
    expect(link).toHaveAttribute('href', 'https://www.ebay.com/itm/1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows no vendor link when the order carries no source url', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()
    await screen.findByText('CC-000412')

    expect(screen.queryByRole('link', { name: 'Vendor page' })).not.toBeInTheDocument()
  })
})
