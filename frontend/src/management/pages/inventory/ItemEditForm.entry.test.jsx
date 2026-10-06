import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    previewItem: vi.fn(),
    getItemErrors: vi.fn(),
    setItemReview: vi.fn(),
    listItemImages: vi.fn(),
    getOfferTitles: vi.fn(),
    listStorageLocations: vi.fn(),
    getItemSales: vi.fn(),
    getItemHistory: vi.fn(),
    listListings: vi.fn(),
    listSalesVenues: vi.fn(),
  },
}))

// The lookup is tested on its own; here it says what note it was handed.
vi.mock('../receiving/FriedbergLookup', () => ({
  default: ({ item }) => (
    <p data-testid="lookup">
      {[
        item.denomination,
        item.series_year,
        item.series_letter,
        item.seal_color,
        item.note_type,
        item.face_plate_number,
        item.back_plate_number,
      ].join('|')}
    </p>
  ),
}))

import { api } from '../../api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import ItemEditForm from './ItemEditForm'

const entry = (code, label, extra = {}) => ({ code, label, source: 'seeded', extra })

const NOTE = {
  id: 12,
  item_code: 'CC-000012',
  item_kind: 'currency',
  source_title: 'A note',
  description: '',
  version: 1,
  reviewed: [],
  derived: {},
  attributes: [],
  denomination: null,
  series_year: null,
  series_letter: null,
  serial_number: null,
  note_type: null,
  seal_color: null,
  face_plate_number: null,
  back_plate_number: null,
  listing_url: null,
  sellers_item_id: null,
}

const reference = () =>
  emptyReference({
    tables: {
      item_kind: [entry('coin', 'Coin'), entry('currency', 'Currency')],
      denomination: [entry('usd_note_1', '$1 Bill', { kind: 'note' })],
      note_type: [
        entry('silver_certificate', 'Silver Certificate'),
        entry('frn', 'FRN'),
      ],
      seal_color: [entry('blue', 'Blue Seal'), entry('green', 'Green Seal')],
    },
  })

async function open(overrides = {}) {
  api.getInventoryItem.mockResolvedValue({ ...NOTE, ...overrides })
  renderWithProviders(
    <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
    {
      reference: reference(),
    },
  )
  await screen.findByDisplayValue('A note')
  return userEvent.setup()
}

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

describe('ItemEditForm: entering an item', () => {
  it('asks for the listing right after the title, then the price, then the facts', async () => {
    await open()
    const labels = [...document.querySelectorAll('.field')].map((row) =>
      row.textContent.replace(/\s+/g, ' ').trim(),
    )
    const at = (text) => labels.findIndex((label) => label.startsWith(text))

    expect(at('Title')).toBe(0)
    expect(at('Listing web address')).toBe(1)
    expect(at("Seller's item id")).toBe(2)
    expect(at('Item cost')).toBeGreaterThan(at("Seller's item id"))
    expect(at('Kind')).toBeGreaterThan(at('Item cost'))
    expect(at('Denomination')).toBeGreaterThan(at('Kind'))
    expect(at('Series year')).toBeGreaterThan(at('Denomination'))
    expect(at('Serial number')).toBeGreaterThan(at('Series year'))
    // What the facts decide comes after them, and the description last.
    expect(at('Note class')).toBeGreaterThan(at('Serial number'))
    expect(at('Description')).toBeGreaterThan(at('Note class'))
  })

  it("fills the seller's item id from the listing's address", async () => {
    const user = await open()
    const listing = screen.getByRole('textbox', { name: 'Listing web address' })
    const seller = screen.getByRole('textbox', { name: "Seller's item id" })

    await user.type(listing, 'https://www.ebay.com/itm/198540692290')
    expect(seller).toHaveValue('198540692290')

    // Another listing: the id the address gave follows it.
    await user.clear(listing)
    expect(seller).toHaveValue('')
    await user.type(listing, 'https://hibid.com/lot/4455/a-note')
    expect(seller).toHaveValue('4455')

    await user.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({
          listing_url: 'https://hibid.com/lot/4455/a-note',
          sellers_item_id: '4455',
        }),
      ),
    )
  })

  it("leaves a seller's item id typed by hand when the address carries none", async () => {
    const user = await open({ sellers_item_id: 'LOT-7' })

    await user.type(
      screen.getByRole('textbox', { name: 'Listing web address' }),
      'https://www.ebay.com/itm/198540692290',
    )

    expect(screen.getByRole('textbox', { name: "Seller's item id" })).toHaveValue(
      'LOT-7',
    )
  })

  it('shows what the facts decide as they are entered, and lets it be typed over', async () => {
    const user = await open()
    api.previewItem.mockImplementation(async (id, { changes }) => ({
      ...NOTE,
      ...changes,
      note_type: 'silver_certificate',
      seal_color: 'blue',
      derived: { note_type_id: 'note_issue', seal_color_id: 'note_issue' },
    }))
    const noteClass = screen.getByRole('combobox', { name: 'note_type' })
    expect(noteClass).toHaveValue('')

    await user.type(screen.getByRole('spinbutton', { name: 'Series year' }), '1957')

    await waitFor(() => expect(noteClass).toHaveValue('silver_certificate'))
    expect(api.previewItem).toHaveBeenLastCalledWith(12, {
      // As the box holds it; the server reads the number.
      changes: { series_year: '1957' },
    })
    expect(screen.getByRole('combobox', { name: 'seal_color' })).toHaveValue('blue')
    expect(screen.getAllByText('suggested').length).toBeGreaterThanOrEqual(2)

    // Chosen by hand, it is the person's: sent, and no longer marked.
    await user.selectOptions(noteClass, 'frn')
    await user.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ series_year: '1957', note_type: 'frn' }),
      ),
    )
    // What the facts filled is not sent: Save fills it by the same rules.
    expect(api.updateInventoryItem.mock.calls[0][1]).not.toHaveProperty('seal_color')
  })

  it('keeps what is shown when the facts typed so far cannot be saved', async () => {
    const user = await open({ note_type: 'frn' })
    api.previewItem.mockRejectedValue(new Error('series_year: out of range'))

    await user.type(screen.getByRole('spinbutton', { name: 'Series year' }), '19')

    await waitFor(() => expect(api.previewItem).toHaveBeenCalled())
    expect(screen.getByRole('combobox', { name: 'note_type' })).toHaveValue('frn')
    expect(screen.queryByText(/out of range/)).toBeNull()
  })

  it('hands the Friedberg lookup the note as the form shows it', async () => {
    const user = await open({ denomination: 'usd_note_1' })
    await user.type(screen.getByRole('spinbutton', { name: 'Series year' }), '1957')
    await user.type(screen.getByRole('textbox', { name: 'Series letter' }), 'B')
    await user.type(screen.getByRole('textbox', { name: 'Face plate' }), 'E82')
    await user.type(screen.getByRole('textbox', { name: 'Back plate' }), '153')

    await user.click(screen.getByRole('button', { name: 'Look up' }))

    // Typed here and not saved yet: the lookup narrows by them all the same.
    expect(screen.getByTestId('lookup')).toHaveTextContent(
      'usd_note_1|1957|B|||E82|153',
    )
  })
})
