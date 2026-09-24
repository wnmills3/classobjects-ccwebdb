import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    getItemSales: vi.fn(),
    getItemHistory: vi.fn(),
    getSuggestedDescription: vi.fn(),
    updateInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
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
  },
}))

import { api } from '../../api'
import { FIELD_HELP } from '../../fieldHelp'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import FilterPanel from './FilterPanel'
import InventoryTable from './InventoryTable'
import ItemEditForm from './ItemEditForm'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

/**
 * Every field a person can fill in explains itself (owner, 2026-09-24: the
 * find-and-list pickers and the search panel's dropdowns had no help).
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
    .filter(({ key }) => !key || !FIELD_HELP[key])
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
      reviewed: [],
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
