import userEvent from '@testing-library/user-event'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    previewItem: vi.fn(),
    suggestDescriptionFromScreen: vi.fn(),
    getItemErrors: vi.fn(),
    setItemErrors: vi.fn(),
    listItemImages: vi.fn(),
    getOfferTitles: vi.fn(),
    listStorageLocations: vi.fn(),
    getItemSales: vi.fn(),
    getItemHistory: vi.fn(),
    listListings: vi.fn(),
    listSalesVenues: vi.fn(),
    clearFriedberg: vi.fn(),
    attachFriedberg: vi.fn(),
  },
}))

import { api } from '../../api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import ItemEditForm from './ItemEditForm'

const entry = (code, label, extra = {}) => ({ code, label, source: 'seeded', extra })

const COIN = {
  id: 12,
  item_code: 'C-012',
  item_kind: 'coin',
  source_title: 'Dime',
  description: 'Mercury Dime',
  version: 1,
  derived: {},
  attributes: [],
  denomination: null,
  year_start: 1965,
  year_end: 1965,
  no_date: false,
  metal: 'silver',
  series: null,
  listing_url: null,
  sellers_item_id: null,
}

const NOTE = {
  ...COIN,
  item_kind: 'currency',
  year_start: null,
  year_end: null,
  metal: null,
  serial_number: 'F06566560R',
  seal_color: 'blue',
}

const reference = () =>
  emptyReference({
    tables: {
      item_kind: [
        entry('coin', 'Coin'),
        entry('bullion', 'Bullion'),
        entry('currency', 'Currency'),
      ],
      metal: [entry('silver', 'Silver'), entry('copper', 'Copper')],
      seal_color: [entry('blue', 'Blue Seal'), entry('green', 'Green Seal')],
      bullion_form: [entry('bar', 'Bar')],
    },
  })

/** A promise and the two functions that settle it. */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

/** An error as the API client raises one, carrying its HTTP status. */
const refusal = (status, message) => Object.assign(new Error(message), { status })

async function open(item = COIN, props = {}) {
  api.getInventoryItem.mockResolvedValue(item)
  renderWithProviders(
    <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} {...props} />,
    { reference: reference() },
  )
  await screen.findByDisplayValue('Mercury Dime')
  return userEvent.setup()
}

const saveButton = () => screen.getByRole('button', { name: 'Save' })
const sent = () => api.updateInventoryItem.mock.calls[0][1]

beforeEach(() => {
  vi.clearAllMocks()
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.listStorageLocations.mockResolvedValue([])
  api.getItemSales.mockResolvedValue([])
  api.getItemHistory.mockResolvedValue([])
  api.listListings.mockResolvedValue([])
  api.listSalesVenues.mockResolvedValue([])
  api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
  api.listItemImages.mockResolvedValue([])
  api.updateInventoryItem.mockResolvedValue({})
  // Never answers unless a test says what the facts decide.
  api.previewItem.mockReturnValue(new Promise(() => {}))
})

describe('ItemEditForm: a preview and a change made elsewhere', () => {
  it('shows the other change at once, not the preview taken before it', async () => {
    const user = await open()
    api.previewItem.mockImplementation(async (id, { changes }) => ({
      ...COIN,
      ...changes,
    }))
    await user.type(screen.getByRole('textbox', { name: /Description/ }), '!')
    await waitFor(() => expect(api.previewItem).toHaveBeenCalledTimes(1))
    // Let the preview land before anyone else saves.
    await waitFor(() =>
      expect(api.previewItem.mock.results[0].value).resolves.toBeTruthy(),
    )

    // Somebody else renames it; the next preview is still on its way.
    api.previewItem.mockReturnValue(new Promise(() => {}))
    api.getInventoryItem.mockResolvedValue({
      ...COIN,
      version: 2,
      source_title: 'Mercury Dime 1943',
    })
    fireEvent.focus(window)

    expect(await screen.findByDisplayValue('Mercury Dime 1943')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Dime')).toBeNull()
    // What the facts decide is asked again of the item as it now stands.
    await waitFor(() => expect(api.previewItem).toHaveBeenCalledTimes(2))
  })

  it('keeps the last preview when the facts typed next are refused', async () => {
    const user = await open({ ...COIN, metal: null })
    api.previewItem.mockResolvedValueOnce({
      ...COIN,
      metal: 'silver',
      derived: { metal_id: 'composition' },
    })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    const metal = screen.getByRole('combobox', { name: 'metal' })
    await waitFor(() => expect(metal).toHaveValue('silver'))

    api.previewItem.mockRejectedValue(refusal(422, 'year_start: out of range'))
    await user.type(year, '9999')
    await waitFor(() => expect(api.previewItem).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(api.previewItem.mock.results[1].value).rejects.toThrow())

    expect(metal).toHaveValue('silver')
    expect(screen.queryByText(/out of range/)).toBeNull()
  })

  it('shows nothing from a preview once asking again has failed outright', async () => {
    const user = await open({ ...COIN, metal: null })
    api.previewItem.mockResolvedValueOnce({
      ...COIN,
      metal: 'silver',
      derived: { metal_id: 'composition' },
    })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    const metal = screen.getByRole('combobox', { name: 'metal' })
    await waitFor(() => expect(metal).toHaveValue('silver'))

    // Not a refusal of the change: the server could not be asked at all.
    api.previewItem.mockRejectedValue(refusal(500, 'Internal Server Error'))
    await user.type(year, '1964')

    await waitFor(() => expect(metal).toHaveValue(''))
  })
})

describe('ItemEditForm: an emptied field is sent as emptied', () => {
  it('sends a picker set back to blank as null, so it is held empty', async () => {
    const user = await open()
    await user.selectOptions(screen.getByRole('combobox', { name: 'metal' }), '')
    await user.click(saveButton())
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    expect(sent().metal).toBeNull()
  })

  it("sends a note's emptied picker and serial number as null", async () => {
    const user = await open(NOTE)
    await user.selectOptions(screen.getByRole('combobox', { name: 'seal_color' }), '')
    await user.clear(screen.getByRole('textbox', { name: 'Serial number' }))
    await user.click(saveButton())
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    expect(sent().seal_color).toBeNull()
    expect(sent().serial_number).toBeNull()
  })
})

describe('ItemEditForm: a range of years', () => {
  it('sends the end year shown when only the start is changed', async () => {
    const user = await open()
    await user.click(screen.getByRole('checkbox', { name: 'Range of years' }))
    expect(screen.getByRole('spinbutton', { name: 'Year to' })).toHaveValue(1965)

    const from = screen.getByRole('spinbutton', { name: 'Year from' })
    await user.clear(from)
    await user.type(from, '1960')
    await user.click(saveButton())

    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    // Both ends: a start sent alone moves a single year's end with it.
    expect(sent()).toMatchObject({ year_start: '1960', year_end: 1965 })
  })
})

describe('ItemEditForm: no date', () => {
  it('unticking puts the years back, so tick and untick is no change', async () => {
    const user = await open()
    const box = screen.getByRole('checkbox', { name: 'No date' })
    await user.click(box)
    await user.click(box)
    expect(screen.getByRole('spinbutton', { name: 'Year' })).toHaveValue(1965)
    expect(saveButton()).toBeDisabled()
  })

  it('is not sent for a piece that is then made a note', async () => {
    const user = await open()
    await user.click(screen.getByRole('checkbox', { name: 'No date' }))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_kind' }),
      'currency',
    )
    await user.click(saveButton())
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    // The server refuses the flag on a note, and a note shows no box for it.
    expect(sent()).not.toHaveProperty('no_date')
    expect(sent().item_kind).toBe('currency')
  })
})

describe('ItemEditForm: changing the kind of a bar', () => {
  it('empties its bullion form when it becomes a note, and says so', async () => {
    const user = await open({ ...COIN, item_kind: 'bullion', bullion_form: 'bar' })
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_kind' }),
      'currency',
    )
    expect(screen.getByText(/cannot keep/)).toHaveTextContent('bar')
    await user.click(saveButton())
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    expect(sent().bullion_form).toBeNull()
  })
})

describe("ItemEditForm: the seller's item id in the heading", () => {
  it('links it to eBay when the listing is an eBay one', async () => {
    await open({
      ...COIN,
      sellers_item_id: '276413586076',
      listing_url: 'https://www.ebay.com/itm/276413586076',
    })
    expect(
      screen.getByRole('link', { name: 'eBay item 276413586076' }),
    ).toHaveAttribute('href', 'https://www.ebay.com/itm/276413586076')
  })

  it('names it without an eBay link when it was bought elsewhere', async () => {
    await open({
      ...COIN,
      vendor: 'hibid.com',
      sellers_item_id: '4455',
      listing_url: 'https://hibid.com/lot/4455/a-dime',
    })
    expect(screen.queryByRole('link', { name: /eBay item/ })).toBeNull()
    expect(screen.getByText("Seller's item id 4455")).toBeInTheDocument()
  })
})

describe('ItemEditForm: what is typed while a request is out', () => {
  it('keeps a field typed while a description was being suggested', async () => {
    const user = await open()
    const asked = deferred()
    api.suggestDescriptionFromScreen.mockReturnValue(asked.promise)
    await user.click(screen.getByRole('button', { name: 'Suggest description' }))
    await user.type(screen.getByRole('textbox', { name: /Title/ }), ' 1943')
    asked.resolve({ description: 'A Mercury dime' })

    expect(await screen.findByDisplayValue('A Mercury dime')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Dime 1943')).toBeInTheDocument()
  })

  it('keeps a field typed while Save was out, and stays open to save it', async () => {
    const onSaved = vi.fn()
    const onChanged = vi.fn()
    const user = await open(COIN, { onSaved, onChanged })
    const saving = deferred()
    api.updateInventoryItem.mockReturnValue(saving.promise)
    await user.type(screen.getByRole('textbox', { name: /Description/ }), '!')
    await user.click(saveButton())
    await user.type(screen.getByRole('textbox', { name: /Title/ }), ' 1943')
    api.getInventoryItem.mockResolvedValue({
      ...COIN,
      version: 2,
      description: 'Mercury Dime!',
    })
    saving.resolve({})

    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('Dime 1943')).toBeInTheDocument()
    expect(saveButton()).toBeEnabled()
  })
})

describe('ItemEditForm: a Friedberg number cleared before a refused save', () => {
  it('reads the note again, so the number is not shown as still attached', async () => {
    const onChanged = vi.fn()
    const attached = {
      ...NOTE,
      friedberg_id: 9,
      friedberg_number: '9901',
      friedberg_status: 'proposed',
      friedberg_verified: false,
    }
    const user = await open(attached, { onChanged })
    api.clearFriedberg.mockResolvedValue(null)
    api.updateInventoryItem.mockRejectedValue(refusal(422, 'Unknown seal_color'))
    api.getInventoryItem.mockResolvedValue({
      ...attached,
      friedberg_id: null,
      friedberg_number: null,
      friedberg_status: null,
    })
    await user.click(screen.getByRole('button', { name: /^clear$/i }))
    await user.type(screen.getByRole('textbox', { name: /Description/ }), '!')
    await user.click(saveButton())

    expect(await screen.findByText('Unknown seal_color')).toBeInTheDocument()
    expect(await screen.findByText('No number attached.')).toBeInTheDocument()
    // The edit that was refused is still there to correct and save.
    expect(screen.getByDisplayValue('Mercury Dime!')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })
})

describe('ItemEditForm: the history after a save that moves no version', () => {
  it('reads the history again when only the errors were saved', async () => {
    const errorTypes = emptyReference({
      tables: {
        item_kind: [entry('coin', 'Coin')],
        error_type: [entry('off_center', 'Off-center', { applies_to: 'coin' })],
      },
    })
    api.getInventoryItem.mockResolvedValue(COIN)
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ItemEditForm itemId={12} />, { reference: errorTypes })
    await screen.findByDisplayValue('Mercury Dime')
    await waitFor(() => expect(api.getItemHistory).toHaveBeenCalledTimes(1))
    const user = userEvent.setup()

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'off_center',
    )
    await user.click(screen.getByRole('button', { name: 'Add error' }))
    await user.click(saveButton())

    await waitFor(() => expect(api.setItemErrors).toHaveBeenCalled())
    // The item's version is as it was: only the save itself says to look.
    await waitFor(() => expect(api.getItemHistory).toHaveBeenCalledTimes(2))
  })
})
