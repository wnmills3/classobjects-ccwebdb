import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    createInventoryItem: vi.fn(),
    getInventoryItem: vi.fn(),
    deleteInventoryItem: vi.fn(),
  },
}))

// The editor is tested on its own; here it stands in for the dialog this
// opens, and offers the three things an editor can tell its opener.
vi.mock('../inventory/ItemEditDialog', () => ({
  default: ({ itemId, onSaved, onChanged, onClose }) => (
    <div role="dialog" aria-label="Edit item">
      editing item {itemId}
      <button onClick={onSaved}>stub save</button>
      <button onClick={onChanged}>stub partial save</button>
      <button onClick={onClose}>stub close</button>
    </div>
  ),
}))

import { api } from '../../api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import AddItem from './AddItem'

const KINDS = [
  { code: 'coin', label: 'Coin', source: 'seeded', extra: {} },
  { code: 'currency', label: 'Currency', source: 'seeded', extra: {} },
]
const DEFAULTS = { tax_rate: '0.0635', tax_includes_shipping: true }

let onChanged

function renderRow(props = {}) {
  onChanged = vi.fn()
  renderWithProviders(
    <AddItem
      purchaseOrderId={22}
      defaults={DEFAULTS}
      onChanged={onChanged}
      {...props}
    />,
    { reference: emptyReference({ tables: { item_kind: KINDS } }) },
  )
}

async function addNote(user, title = 'A note') {
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'item_kind' }),
    'currency',
  )
  await user.type(screen.getByRole('textbox', { name: 'Title' }), title)
  await user.click(screen.getByRole('button', { name: 'Add item' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.createInventoryItem.mockResolvedValue({ id: 30, item_code: 'CC-000030' })
  api.deleteInventoryItem.mockResolvedValue(null)
})

describe('AddItem', () => {
  it('makes the item from its kind and title and opens it in the editor', async () => {
    const user = userEvent.setup()
    renderRow({ orderUrl: 'https://hibid.com/lot/12' })
    // Nothing to add until it has a title.
    expect(screen.getByRole('button', { name: 'Add item' })).toBeDisabled()

    await addNote(user, '  $20 Bill  ')

    expect(await screen.findByRole('dialog', { name: 'Edit item' })).toHaveTextContent(
      'editing item 30',
    )
    expect(api.createInventoryItem).toHaveBeenCalledWith({
      purchase_order_id: 22,
      listing_url: 'https://hibid.com/lot/12',
      item_kind: 'currency',
      source_title: '$20 Bill',
      // Where nearly everything bought comes from; changed in the editor.
      country: 'US',
      tax_rate: '0.0635',
      tax_includes_shipping: true,
    })
    // The title is spent, and a second item waits for the first.
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('')
    expect(onChanged).toHaveBeenCalled()
  })

  it('keeps the item once the editor saves it, and offers another like it', async () => {
    const user = userEvent.setup()
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub save' }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.deleteInventoryItem).not.toHaveBeenCalled()

    api.getInventoryItem.mockResolvedValue({
      id: 30,
      item_code: 'CC-000030',
      item_kind: 'currency',
      source_title: '$20 lot',
      listing_url: 'https://www.ebay.com/itm/1',
      sellers_item_id: '1',
      status: 'received',
      storage_location_id: 4,
      country: 'US',
      denomination: 'usd_note_20',
      series: null,
      series_year: 2017,
      series_letter: 'A',
      seal_color: 'green',
      note_type: 'frn',
      signature_combination: 'carranza_mnuchin',
      fed_district: 'F',
      grading_service: 'PMG',
      metal: null,
      mint: null,
      // Per piece: never copied.
      grade: '65',
      serial_number: 'PF55577555G',
      item_cost: '60.00',
    })
    api.createInventoryItem.mockResolvedValue({ id: 31, item_code: 'CC-000031' })
    await user.click(screen.getByRole('button', { name: 'Add another like CC-000030' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenLastCalledWith({
        purchase_order_id: 22,
        item_kind: 'currency',
        source_title: '$20 lot',
        listing_url: 'https://www.ebay.com/itm/1',
        sellers_item_id: '1',
        status: 'received',
        storage_location_id: 4,
        country: 'US',
        denomination: 'usd_note_20',
        series_year: 2017,
        series_letter: 'A',
        seal_color: 'green',
        note_type: 'frn',
        signature_combination: 'carranza_mnuchin',
        fed_district: 'F',
        grading_service: 'PMG',
        tax_rate: '0.0635',
        tax_includes_shipping: true,
      }),
    )
    expect(await screen.findByRole('dialog')).toHaveTextContent('editing item 31')
  })

  it("copies a coin's metal and mint as a person set them, and none of a note's fields", async () => {
    const user = userEvent.setup()
    renderRow()
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'A dollar')
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    await user.click(await screen.findByRole('button', { name: 'stub save' }))

    api.getInventoryItem.mockResolvedValue({
      id: 30,
      item_kind: 'coin',
      source_title: 'A dollar',
      metal: 'silver',
      mint: 'S',
      // A coin holds none of these; were they there, they are not sent.
      series_year: 1881,
      note_type: 'frn',
    })
    await user.click(screen.getByRole('button', { name: 'Add another like CC-000030' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenLastCalledWith({
        purchase_order_id: 22,
        item_kind: 'coin',
        source_title: 'A dollar',
        metal: 'silver',
        mint: 'S',
        tax_rate: '0.0635',
        tax_includes_shipping: true,
      }),
    )
  })

  it('leaves what a rule filled on the last item for the rules to fill on the next', async () => {
    const user = userEvent.setup()
    renderRow()
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'A dime')
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    await user.click(await screen.findByRole('button', { name: 'stub save' }))

    api.getInventoryItem.mockResolvedValue({
      id: 30,
      item_kind: 'coin',
      source_title: 'A dime',
      denomination: 'usd_coin_0_10',
      // Typed by a person: shared with the next piece.
      mint: 'S',
      // Filled from the year, which the next piece does not share. Sent as
      // typed they would be held against the next piece's own year.
      metal: 'silver',
      series: 'roosevelt_dime',
      derived: { metal_id: 'composition', series_id: 'series_classify' },
    })
    await user.click(screen.getByRole('button', { name: 'Add another like CC-000030' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenLastCalledWith({
        purchase_order_id: 22,
        item_kind: 'coin',
        source_title: 'A dime',
        denomination: 'usd_coin_0_10',
        mint: 'S',
        tax_rate: '0.0635',
        tax_includes_shipping: true,
      }),
    )
  })

  it("leaves a note's Bank read from its serial, and copies the class typed", async () => {
    const user = userEvent.setup()
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub save' }))

    api.getInventoryItem.mockResolvedValue({
      id: 30,
      item_kind: 'currency',
      source_title: 'A note',
      note_type: 'frn',
      seal_color: 'green',
      fed_district: 'F',
      derived: { fed_district_id: 'serial_district', seal_color_id: 'note_issue' },
    })
    await user.click(screen.getByRole('button', { name: 'Add another like CC-000030' }))

    await waitFor(() =>
      expect(api.createInventoryItem).toHaveBeenLastCalledWith({
        purchase_order_id: 22,
        item_kind: 'currency',
        source_title: 'A note',
        note_type: 'frn',
        tax_rate: '0.0635',
        tax_includes_shipping: true,
      }),
    )
  })

  it('starts the next piece as ordered when the last was marked missing', async () => {
    const user = userEvent.setup()
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub save' }))

    api.getInventoryItem.mockResolvedValue({
      id: 30,
      item_kind: 'currency',
      source_title: 'A note',
      // Not a status an item can be entered with: the server refuses it.
      status: 'missing',
    })
    await user.click(screen.getByRole('button', { name: 'Add another like CC-000030' }))

    await waitFor(() => expect(api.createInventoryItem).toHaveBeenCalledTimes(2))
    expect(api.createInventoryItem.mock.calls[1][0]).not.toHaveProperty('status')
  })

  it('removes an item whose editor was closed without a save', async () => {
    const user = userEvent.setup()
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub close' }))

    await waitFor(() => expect(api.deleteInventoryItem).toHaveBeenCalledWith(30))
    expect(screen.queryByRole('dialog')).toBeNull()
    // Never entered: nothing to add another like.
    expect(screen.queryByRole('button', { name: /Add another like/ })).toBeNull()
  })

  it('keeps an item a save wrote part of, even when the editor is then closed', async () => {
    const user = userEvent.setup()
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub partial save' }))
    await user.click(screen.getByRole('button', { name: 'stub close' }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.deleteInventoryItem).not.toHaveBeenCalled()
    expect(
      screen.getByRole('button', { name: 'Add another like CC-000030' }),
    ).toBeInTheDocument()
  })

  it('says so when the item could not be made, and keeps the title', async () => {
    const user = userEvent.setup()
    api.createInventoryItem.mockRejectedValue(new Error('Unknown purchase order'))
    renderRow()
    await addNote(user, 'A note')

    expect(await screen.findByText('Unknown purchase order')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('A note')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says so when an abandoned item could not be removed', async () => {
    const user = userEvent.setup()
    api.deleteInventoryItem.mockRejectedValue(new Error('offline'))
    renderRow()
    await addNote(user)
    await user.click(await screen.findByRole('button', { name: 'stub close' }))

    expect(
      await screen.findByText('CC-000030 was not removed: offline'),
    ).toBeInTheDocument()
  })

  it('adds nothing while the purchase has a reason not to', async () => {
    const user = userEvent.setup()
    renderRow({ disabledReason: 'Fix the tax rate above before saving items.' })
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'A note{Enter}')

    expect(
      screen.getByText('Fix the tax rate above before saving items.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add item' })).toBeDisabled()
    expect(api.createInventoryItem).not.toHaveBeenCalled()
  })
})
