import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    updateInventoryItem: vi.fn(),
    previewItem: vi.fn(),
    getItemErrors: vi.fn(),
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
    // The first facts decide a class other than the stored one; the next
    // keystrokes are refused. What the first decided is what stays shown.
    api.previewItem.mockResolvedValueOnce({
      ...NOTE,
      series_year: 1957,
      note_type: 'silver_certificate',
      derived: { note_type_id: 'note_issue' },
    })
    const year = screen.getByRole('spinbutton', { name: 'Series year' })
    const noteClass = screen.getByRole('combobox', { name: 'note_type' })
    await user.type(year, '1957')
    await waitFor(() => expect(noteClass).toHaveValue('silver_certificate'))

    api.previewItem.mockRejectedValue(
      Object.assign(new Error('series_year: out of range'), { status: 422 }),
    )
    await user.type(year, '9')

    await waitFor(() => expect(api.previewItem).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(api.previewItem.mock.results[1].value).rejects.toThrow())
    expect(noteClass).toHaveValue('silver_certificate')
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

//: A piece that is not a note, with nothing recorded but its kind and title.
const PIECE = {
  id: 12,
  item_code: 'CC-000012',
  item_kind: 'coin',
  source_title: 'A piece',
  description: '',
  version: 1,
  derived: {},
  attributes: [],
  denomination: null,
  year_start: null,
  year_end: null,
  mint: null,
  variety: null,
  series: null,
  metal: null,
  bullion_form: null,
  set_form: null,
  fineness: null,
  gross_weight_ozt: null,
  fine_weight_ozt: null,
  weight_note: null,
  listing_url: null,
  sellers_item_id: null,
}

const pieceReference = () =>
  emptyReference({
    tables: {
      item_kind: [
        entry('coin', 'Coin'),
        entry('bullion', 'Bullion'),
        entry('set', 'Set'),
        entry('medal', 'Medal'),
        entry('currency', 'Currency'),
      ],
      denomination: [entry('usd_coin_1_00', 'Dollar', { kind: 'coin' })],
      series: [entry('morgan_dollar', 'Morgan Dollar', { applies_to: 'coin' })],
      metal: [entry('silver', 'Silver'), entry('copper', 'Copper')],
      mint: [entry('S', 'San Francisco')],
    },
  })

async function openPiece(overrides = {}) {
  api.getInventoryItem.mockResolvedValue({ ...PIECE, ...overrides })
  renderWithProviders(
    <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
    { reference: pieceReference() },
  )
  await screen.findByDisplayValue('A piece')
  return userEvent.setup()
}

/** The form's fields in the order shown, each by its help topic. */
function fieldOrder() {
  return [...document.querySelectorAll('.field[data-help]')].map(
    (row) => row.dataset.help,
  )
}

/** Whether the fields named appear in this order, one after another. */
function runOf(order, ...keys) {
  const first = order.indexOf(keys[0])
  return first >= 0 && keys.every((key, n) => order[first + n] === key)
}

//: The four weight fields, in the order `WeightFields` shows them.
const WEIGHTS = ['gross_weight_ozt', 'fineness', 'fine_weight_ozt', 'weight_note']

describe('ItemEditForm: the facts asked for, by kind', () => {
  it('asks a coin for its denomination, year and mint, then shows what they decide', async () => {
    await openPiece()
    const order = fieldOrder()

    // Mint follows the year directly: the two together are the coin.
    expect(runOf(order, 'item_kind', 'denomination', 'year', 'mint')).toBe(true)
    // What those decide comes next, before anything about its condition.
    expect(runOf(order, 'mint', 'series', 'metal', ...WEIGHTS)).toBe(true)
    expect(order.indexOf('strike_type')).toBeGreaterThan(order.indexOf('weight_note'))
    expect(order.indexOf('grade')).toBeGreaterThan(order.indexOf('strike_type'))
    // The description is written from the facts, so it follows them all.
    expect(order.indexOf('description')).toBeGreaterThan(order.indexOf('status'))
  })

  it.each(['bullion', 'medal', 'token'])(
    'asks %s for its form, metal and weight before a year or a face value',
    async (kind) => {
      await openPiece({ item_kind: kind })
      const order = fieldOrder()

      expect(
        runOf(
          order,
          'item_kind',
          'bullion_form',
          'metal',
          ...WEIGHTS,
          'year',
          'mint',
          'denomination',
          'series',
        ),
      ).toBe(true)
      // Asked for once: metal and the weights are not shown again as decided.
      expect(order.filter((key) => key === 'metal')).toHaveLength(1)
      expect(order.filter((key) => key === 'fineness')).toHaveLength(1)
      expect(order.indexOf('grade')).toBeGreaterThan(order.indexOf('series'))
    },
  )

  it('asks a set what kind of set it is, of which year, from which mint', async () => {
    await openPiece({ item_kind: 'set' })
    const order = fieldOrder()

    expect(runOf(order, 'item_kind', 'set_form', 'year', 'mint', 'denomination')).toBe(
      true,
    )
    expect(runOf(order, 'denomination', 'series', 'metal', ...WEIGHTS)).toBe(true)
  })

  it('asks a kind it has no order for as it asks a coin', async () => {
    await openPiece({ item_kind: 'other' })
    expect(runOf(fieldOrder(), 'item_kind', 'denomination', 'year', 'mint')).toBe(true)
  })

  it('shows every field once, whatever the kind', async () => {
    await openPiece({ item_kind: 'bullion' })
    const order = fieldOrder()
    expect(new Set(order).size).toBe(order.length)
    // Nothing a coin's form holds went missing in the reordering.
    for (const key of [
      'denomination',
      'series',
      'set_form',
      'country',
      'status',
      'strike_type',
      'grading_service',
      'grade_designation',
      'variety',
    ]) {
      expect(order).toContain(key)
    }
  })

  it('follows the kind as it is changed, before any save', async () => {
    const user = await openPiece()
    expect(runOf(fieldOrder(), 'item_kind', 'denomination')).toBe(true)

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_kind' }),
      'bullion',
    )

    expect(runOf(fieldOrder(), 'item_kind', 'bullion_form', 'metal')).toBe(true)
  })

  it('leaves a note asked for as it was: denomination, then its own fields', async () => {
    await open()
    const order = fieldOrder()
    expect(runOf(order, 'item_kind', 'denomination')).toBe(true)
    expect(order).not.toContain('year')
    expect(order).not.toContain('mint')
    expect(order).not.toContain('fineness')
    // A note's own fields say what its facts decide; its design is not
    // pulled up ahead of its grade as a coin's is.
    expect(order.indexOf('series')).toBeGreaterThan(order.indexOf('grade'))
  })

  it("shows what a coin's facts decide as they are entered", async () => {
    const user = await openPiece({ denomination: 'usd_coin_1_00' })
    api.previewItem.mockImplementation(async (id, { changes }) => ({
      ...PIECE,
      denomination: 'usd_coin_1_00',
      ...changes,
      series: 'morgan_dollar',
      metal: 'silver',
      fineness: '0.9000',
      derived: {
        series_id: 'series_facts',
        metal_id: 'composition',
        fineness: 'composition',
      },
    }))
    const series = screen.getByRole('combobox', { name: 'series' })
    expect(series).toHaveValue('')

    await user.type(screen.getByRole('spinbutton', { name: 'Year' }), '1881')

    await waitFor(() => expect(series).toHaveValue('morgan_dollar'))
    expect(screen.getByRole('combobox', { name: 'metal' })).toHaveValue('silver')
    expect(screen.getByDisplayValue('0.9000')).toBeInTheDocument()
    expect(screen.getAllByText('suggested').length).toBeGreaterThanOrEqual(3)

    // What the facts filled is not sent: Save fills it by the same rules.
    await user.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    const sent = api.updateInventoryItem.mock.calls[0][1]
    expect(sent).not.toHaveProperty('series')
    expect(sent).not.toHaveProperty('metal')
    expect(sent).not.toHaveProperty('fineness')
  })
})
