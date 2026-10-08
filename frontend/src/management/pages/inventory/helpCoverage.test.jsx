import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    getItemSales: vi.fn(),
    getItemHistory: vi.fn(),
    suggestDescriptionFromScreen: vi.fn(),
    updateInventoryItem: vi.fn(),
    getItemErrors: vi.fn(),
    setItemErrors: vi.fn(),
    listListings: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
    getOfferTitles: vi.fn(),
    createOffers: vi.fn(),
    listItemImages: vi.fn(),
    uploadImage: vi.fn(),
    updateImageLink: vi.fn(),
    detachImage: vi.fn(),
    getSignatureChoices: vi.fn(),
    searchFriedberg: vi.fn(),
    previewItem: vi.fn(),
    // The bulk bar's location picker, and its lot dialog's list of lots.
    listStorageLocations: vi.fn(),
    listLots: vi.fn(),
    createLot: vi.fn(),
    updateLot: vi.fn(),
  },
}))

import userEvent from '@testing-library/user-event'

import { api } from '../../api'
import { FIELD_HELP } from '../../fieldHelp'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import BulkEditBar from './BulkEditBar'
import FilterPanel from './FilterPanel'
import InventoryTable from './InventoryTable'
import ItemEditForm from './ItemEditForm'
import OfferDialog from './OfferDialog'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

/**
 * Every field a person can fill in explains itself.
 *
 * Walks every input, dropdown and text box actually rendered and requires a
 * `data-help` topic above it that has text. A literal-key scan of the source
 * cannot see a key built at runtime -- `data-help={helpFor(param)}` -- nor a
 * control added with no key at all, which is the gap this closes.
 */
function uncovered(container) {
  return [...container.querySelectorAll('input, select, textarea')]
    .filter((control) => control.type !== 'hidden')
    .map((control) => ({
      control,
      key: control.closest('[data-help]')?.dataset.help ?? null,
    }))
    .filter(({ key }) => !key || !FIELD_HELP[key]?.title || !FIELD_HELP[key]?.text)
    .map(
      ({ control, key }) =>
        `${control.tagName.toLowerCase()} ${control.getAttribute('aria-label') ?? control.name ?? ''} (${key ?? 'no topic'})`,
    )
}

const vocabularies = emptyReference({
  tables: {
    item_kind: [
      { code: 'coin', label: 'Coin', source: 'seeded', extra: {} },
      { code: 'currency', label: 'Currency', source: 'seeded', extra: {} },
    ],
    item_attribute: [
      {
        code: 'star',
        label: 'Star Note',
        source: 'seeded',
        extra: { applies_to: 'currency', attribute_group: 'serial' },
      },
    ],
    error_type: [
      {
        code: 'ink_smear',
        label: 'Ink Smear',
        source: 'seeded',
        extra: { applies_to: 'currency' },
      },
    ],
  },
})

beforeEach(() => {
  vi.clearAllMocks()
  api.getItemSales.mockResolvedValue([])
  api.getItemHistory.mockResolvedValue([])
  api.listListings.mockResolvedValue([])
  api.listSalesVenues.mockResolvedValue([])
  api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
  api.listItemImages.mockResolvedValue([])
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.previewItem.mockReturnValue(new Promise(() => {}))
  api.listStorageLocations.mockResolvedValue([{ id: 9, label: 'Home safe' }])
})

describe('Every field has help', () => {
  it.each([
    ['a banknote', { item_kind: 'currency', serial_number: 'F06566560R' }],
    ['a coin', { item_kind: 'coin', mint: 'S' }],
  ])('in the item editor for %s', async (_what, kind) => {
    api.getInventoryItem.mockResolvedValue({
      id: 12,
      item_code: 'CC-000012',
      description: 'Mercury Dime',
      version: 3,
      tax_rate: '0.0635',
      sales_tax: '1.00',
      ...kind,
    })
    const { container } = renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    await screen.findByDisplayValue('Mercury Dime')
    await screen.findByText(/No photographs filed yet/)
    expect(container.querySelectorAll('input, select').length).toBeGreaterThan(20)
    expect(uncovered(container)).toEqual([])
  })

  it('in the rows the editor shows only for a range of years or a recorded error', async () => {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue({
      id: 12,
      item_code: 'CC-000012',
      description: 'Mercury Dime',
      version: 3,
      item_kind: 'currency',
      year_start: 1878,
      year_end: 1878,
    })
    api.getItemErrors.mockResolvedValue({
      inventory_item_id: 12,
      errors: [{ error_type: 'ink_smear', details: 'left margin' }],
    })
    const { container, unmount } = renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    await screen.findByDisplayValue('left margin')
    expect(uncovered(container)).toEqual([])
    unmount()

    api.getInventoryItem.mockResolvedValue({
      id: 12,
      item_code: 'CC-000012',
      description: 'Mercury Dime',
      version: 3,
      item_kind: 'coin',
      year_start: 1878,
      year_end: 1878,
    })
    const coin = renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    await screen.findByDisplayValue('Mercury Dime')
    await user.click(screen.getByRole('checkbox', { name: 'Range of years' }))
    expect(screen.getByRole('spinbutton', { name: 'Year to' })).toBeInTheDocument()
    expect(uncovered(coin.container)).toEqual([])
  })

  it("names the bar's field picker by a topic of its own, not the field chosen", () => {
    renderWithProviders(
      <BulkEditBar view="coins" ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    expect(screen.getByRole('combobox', { name: 'Field to change' })).toHaveAttribute(
      'data-help',
      'bulk_field',
    )
  })

  it.each([
    ['a selection of items', { items: [{ id: 7, item_code: 'CC-000007' }] }],
    ['a lot', { lot: { id: 3, title: 'Three dollars', cost_basis: '50.00' } }],
  ])('in the dialog that offers %s for sale', async (_what, subject) => {
    const { container } = renderWithProviders(
      <OfferDialog {...subject} onOffered={vi.fn()} onClose={vi.fn()} />,
    )
    await screen.findByLabelText('Platform')
    // Platform, format, margin, and a price, title, description and listing
    // number for the row.
    expect(container.querySelectorAll('input, select, textarea')).toHaveLength(7)
    expect(uncovered(container)).toEqual([])
  })

  it('in the dialog that groups a selection into a lot', async () => {
    const user = userEvent.setup()
    api.listLots.mockResolvedValue({ lots: [] })
    const { container } = renderWithProviders(
      <BulkEditBar view="coins" ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />,
    )
    await user.click(screen.getByRole('button', { name: 'Group into lot...' }))
    const dialog = container.querySelector('dialog')
    await screen.findByLabelText('Title')
    expect(dialog.querySelectorAll('input, select')).toHaveLength(2)
    expect(uncovered(dialog)).toEqual([])
  })

  it.each(['coins', 'currency'])(
    'in the bar that changes a selection of %s, whichever field is chosen',
    async (view) => {
      const user = userEvent.setup()
      const { container } = renderWithProviders(
        <BulkEditBar view={view} ids={[1, 2]} onApplied={vi.fn()} onClear={vi.fn()} />,
      )
      const field = screen.getByRole('combobox', { name: 'Field to change' })
      const offered = [...field.options].map((option) => option.value)
      expect(offered.length).toBeGreaterThan(3)
      for (const choice of offered) {
        await user.selectOptions(field, choice)
        if (choice === 'storage_location_id') {
          await screen.findByRole('option', { name: 'Home safe' })
        }
        expect(container.querySelectorAll('input, select').length).toBeGreaterThan(1)
        expect(uncovered(container)).toEqual([])
      }
    },
  )

  it.each([
    ['coins', COIN_VIEW],
    ['currency', CURRENCY_VIEW],
  ])('in the %s search panel and results', (_what, config) => {
    const facets = Object.fromEntries(
      config.facetFilters.map(([, , key]) => [
        key,
        [{ value: 'x', label: 'X', count: 1 }],
      ]),
    )
    const issues = Object.fromEntries(config.issueChecks.map((code) => [code, 1]))
    const { container } = renderWithProviders(
      <>
        <FilterPanel
          config={config}
          current={{}}
          apply={vi.fn()}
          facets={facets}
          issues={issues}
          issueDescriptions={{}}
          total={1}
          busy={false}
          clear={vi.fn()}
        />
        <InventoryTable
          config={config}
          rows={[{ id: 1, item_code: 'CC-000001' }]}
          current={{}}
          apply={vi.fn()}
          selected={[]}
          onSelect={vi.fn()}
          onOpen={vi.fn()}
        />
      </>,
    )
    expect(container.querySelectorAll('input, select').length).toBeGreaterThan(8)
    expect(uncovered(container)).toEqual([])
  })
})
