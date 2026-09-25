import { useState } from 'react'

import userEvent from '@testing-library/user-event'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listSalesVenues: vi.fn(),
    getOfferTitles: vi.fn(),
    createOffers: vi.fn(),
  },
}))

import { api } from '../../api'
import OfferDialog from './OfferDialog'
import {
  estimatedFees,
  hasDefaultFees,
  netAfterFees,
  netMarginPercent,
} from '../platform-rates'
import { ApiError } from '../../../shared/api'
import { renderWithProviders } from '../../../test/helpers'

// Two selected items: one costed, one not. `total_cost` is the cost basis an
// inventory row carries, and it arrives as a decimal string.
const ITEMS = [
  {
    id: 7,
    item_code: 'CC-000007',
    source_title: '1881-S Morgan Dollar',
    description: 'Blast white.',
    total_cost: '120.00',
  },
  {
    id: 9,
    item_code: 'CC-000009',
    source_title: '1923 Peace Dollar',
    description: 'Lightly toned.',
    total_cost: null,
  },
]

// eBay's published defaults; the web store charges itself nothing, which is
// not the same fact as "nobody has recorded what this platform charges".
const VENUES = [
  {
    code: 'store',
    name: 'Web store',
    is_own_store: true,
    is_active: true,
    commission_rate: null,
    processing_rate: null,
    processing_fixed: null,
    listing_fee: null,
  },
  {
    code: 'ebay',
    name: 'eBay',
    is_own_store: false,
    is_active: true,
    commission_rate: '0.1325',
    processing_rate: '0.0290',
    processing_fixed: '0.30',
    listing_fee: null,
  },
]

const LISTING = {
  id: 14,
  item_id: 7,
  item_code: 'CC-000007',
  item_title: '1881-S Morgan Dollar',
  venue: 'ebay',
  venue_name: 'eBay',
  format: 'fixed_price',
  status: 'active',
  price: '189.00',
  currency: 'USD',
  quantity_available: 1,
  title: '1881-S Morgan Dollar',
  description: 'Blast white.',
  external_id: null,
  external_url: null,
  listed_at: '2026-09-18T12:00:00Z',
  ended_at: null,
  paused_by_listing_id: null,
  cost_basis: '120.00',
  version: 1,
}

const rowFor = (code) => screen.getByRole('row', { name: new RegExp(`^${code}`) })

function renderDialog(props = {}, options) {
  return renderWithProviders(
    <OfferDialog
      items={ITEMS}
      onOffered={props.onOffered ?? vi.fn()}
      onClose={props.onClose ?? vi.fn()}
      skipped={props.skipped ?? 0}
    />,
    options,
  )
}

/** Choose eBay and put a price against each item, as the operator would. */
async function fillIn(user, { venue = 'ebay', prices = ['189.00', '99.00'] } = {}) {
  // Waited for the platform's own option, not merely for the picker: the
  // picker renders before the platforms have loaded, and choosing then would
  // be choosing from an empty list.
  await screen.findByRole('option', {
    name: VENUES.find((v) => v.code === venue).name,
  })
  await user.selectOptions(screen.getByLabelText('Platform'), venue)
  const codes = ITEMS.map((item) => item.item_code)
  for (const [index, code] of codes.entries()) {
    if (prices[index] === undefined) continue
    await user.type(screen.getByLabelText(`Price for ${code}`), prices[index])
  }
}

const offerButton = () => screen.getByRole('button', { name: /^Offer 2 for sale$/ })

beforeEach(() => {
  vi.resetAllMocks()
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.listSalesVenues.mockResolvedValue(VENUES)
  api.createOffers.mockResolvedValue({ listings: [LISTING] })
})

describe('OfferDialog', () => {
  it('offers every selected item in one batch, as one platform and one format', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    renderDialog({ onOffered })
    await fillIn(user)
    await user.click(offerButton())

    // Exact, not objectContaining: OfferIn and OfferItemIn both forbid extra
    // fields, `item_id` is an int and `price` a Decimal Pydantic reads from a
    // string -- a float here would lose cents and a stray key is a 422.
    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'ebay',
      format: 'fixed_price',
      items: [
        {
          item_id: 7,
          price: '189.00',
          title: '1881-S Morgan Dollar',
          description: 'Blast white.',
          external_id: null,
        },
        {
          item_id: 9,
          price: '99.00',
          title: '1923 Peace Dollar',
          description: 'Lightly toned.',
          external_id: null,
        },
      ],
    })
    await waitFor(() => expect(onOffered).toHaveBeenCalledWith([LISTING]))
  })

  it('sends an edited title, wording and listing number as typed', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user)
    const title = screen.getByLabelText('Title for CC-000007')
    await user.clear(title)
    await user.type(title, 'Morgan Dollar 1881-S MS64')
    await user.type(screen.getByLabelText('Listing number for CC-000007'), '1234567')
    await user.click(offerButton())

    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'ebay',
      format: 'fixed_price',
      items: [
        {
          item_id: 7,
          price: '189.00',
          title: 'Morgan Dollar 1881-S MS64',
          description: 'Blast white.',
          external_id: '1234567',
        },
        {
          item_id: 9,
          price: '99.00',
          title: '1923 Peace Dollar',
          description: 'Lightly toned.',
          external_id: null,
        },
      ],
    })
  })

  it('starts each title from the suggested title, not the purchase wording', async () => {
    api.getOfferTitles.mockResolvedValue({
      titles: { 7: '1881-S Morgan Dollar PCGS MS64' },
    })
    renderDialog()

    expect(api.getOfferTitles).toHaveBeenCalledWith([7, 9])
    await waitFor(() =>
      expect(screen.getByLabelText('Title for CC-000007')).toHaveValue(
        '1881-S Morgan Dollar PCGS MS64',
      ),
    )
    // No suggestion for this one: it keeps what it had.
    expect(screen.getByLabelText('Title for CC-000009')).toHaveValue(
      '1923 Peace Dollar',
    )
  })

  it('never overwrites a title the operator has already edited', async () => {
    let answer
    api.getOfferTitles.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      }),
    )
    const user = userEvent.setup()
    renderDialog()
    const title = screen.getByLabelText('Title for CC-000007')
    await user.clear(title)
    await user.type(title, 'My own wording')

    await act(async () => answer({ titles: { 7: 'Suggested', 9: 'Also suggested' } }))

    expect(title).toHaveValue('My own wording')
    // The untouched row still takes its suggestion.
    expect(screen.getByLabelText('Title for CC-000009')).toHaveValue('Also suggested')
  })

  it('shows each item’s recorded value beside its cost', async () => {
    renderWithProviders(
      <OfferDialog
        items={[{ ...ITEMS[0], numismatic_value: '245.00' }, ITEMS[1]]}
        onOffered={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    await screen.findByRole('option', { name: 'eBay' })

    expect(screen.getByRole('columnheader', { name: 'Value' })).toBeInTheDocument()
    expect(within(rowFor('CC-000007')).getByText('245.00')).toBeInTheDocument()
    // No value recorded: blank-with-a-mark, not zero.
    const cells = within(rowFor('CC-000009')).getAllByRole('cell')
    const valueColumn = screen
      .getAllByRole('columnheader')
      .findIndex((header) => header.textContent === 'Value')
    expect(cells[valueColumn]).not.toHaveTextContent('0')
  })

  it('says so when the suggested titles cannot be loaded', async () => {
    api.getOfferTitles.mockRejectedValue(new Error('offline'))
    renderDialog()

    expect(
      await screen.findByText(/Suggested titles could not be loaded/),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Title for CC-000007')).toHaveValue(
      '1881-S Morgan Dollar',
    )
  })

  it('offers by auction when that format is chosen', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user)
    await user.selectOptions(screen.getByLabelText('Format'), 'auction')
    await user.click(offerButton())

    expect(api.createOffers).toHaveBeenCalledWith({
      venue: 'ebay',
      format: 'auction',
      items: [
        {
          item_id: 7,
          price: '189.00',
          title: '1881-S Morgan Dollar',
          description: 'Blast white.',
          external_id: null,
        },
        {
          item_id: 9,
          price: '99.00',
          title: '1923 Peace Dollar',
          description: 'Lightly toned.',
          external_id: null,
        },
      ],
    })
  })

  // The price as it was typed, never through a float, and no invented
  // currency code: an inventory row carries no currency of its own.
  it("shows the platform's fees, what is left and the margin", async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user, { prices: ['189.00'] })

    const row = rowFor('CC-000007')
    expect(within(row).getByText('120.00')).toBeInTheDocument()
    expect(within(row).getByText('30.82')).toBeInTheDocument()
    expect(within(row).getByText('158.18')).toBeInTheDocument()
    expect(within(row).getByText('20.2%')).toBeInTheDocument()
  })

  // "Nobody has recorded this platform's fees" is not "this platform is
  // free", and an item nobody has costed has no margin. Both are blank.
  it('shows no fees for a platform with none recorded, and no margin without a cost', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user, { venue: 'store', prices: ['189.00', '99.00'] })

    // No recorded value, no fees, no net.
    expect(within(rowFor('CC-000007')).getAllByText('--')).toHaveLength(3)
    // The uncosted item: no cost, no value, no fees, no net, no margin.
    expect(within(rowFor('CC-000009')).getAllByText('--')).toHaveLength(5)
  })

  // The refusal that matters: the API refuses the whole batch and says why,
  // item by item. A dialog that closed here would take the reasons with it.
  it('lists every refused item with its reason and stays open', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    api.createOffers.mockRejectedValue(
      new ApiError(409, '2 item(s) cannot be offered', {
        detail: '2 item(s) cannot be offered',
        refused: [
          { item_code: 'CC-000007', reason: 'is not received (it is ordered)' },
          {
            item_code: 'CC-000009',
            reason: 'is active on eBay, listing #14: end it first',
          },
        ],
      }),
    )
    renderDialog({ onOffered })
    await fillIn(user)
    await user.click(offerButton())

    expect(await screen.findByText(/is not received \(it is ordered\)/)).toBeVisible()
    expect(screen.getByText(/end it first/)).toBeVisible()
    expect(screen.getByText('2 item(s) cannot be offered')).toBeVisible()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onOffered).not.toHaveBeenCalled()
  })

  // The race the writer cannot attribute: a 409 whose `refused` is empty. It
  // must still say something, and must not throw looking for a list.
  it('shows a refusal that names no item', async () => {
    const user = userEvent.setup()
    api.createOffers.mockRejectedValue(
      new ApiError(409, 'One of those items was offered somewhere else just now', {
        detail: 'One of those items was offered somewhere else just now',
        refused: [],
      }),
    )
    renderDialog()
    await fillIn(user)
    await user.click(offerButton())

    expect(await screen.findByText(/offered somewhere else just now/)).toBeVisible()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('names the items whose price is not an amount, without calling the API', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user, { prices: ['189.00'] })
    await user.click(offerButton())

    expect(api.createOffers).not.toHaveBeenCalled()
    expect(screen.getByText(/CC-000009/, { selector: '.error' })).toBeVisible()
  })

  it('will not offer until a platform is chosen', async () => {
    const user = userEvent.setup()
    renderDialog()
    await screen.findByRole('option', { name: 'eBay' })
    await user.type(screen.getByLabelText('Price for CC-000007'), '189.00')
    await user.type(screen.getByLabelText('Price for CC-000009'), '99.00')
    await user.click(offerButton())

    expect(api.createOffers).not.toHaveBeenCalled()
    // Anchored on the whole sentence: the picker's own blank option reads
    // "Choose a platform" too, and a looser match finds that instead.
    expect(screen.getByText('Choose a platform to offer these on.')).toBeVisible()
  })

  // A selection can span pages; only the rows on this one can be priced, and
  // silently dropping the rest is exactly the kind of quiet default that
  // hides a mistake.
  it('says how many selected items are not on this page', async () => {
    renderDialog({ skipped: 3 })
    expect(
      await screen.findByText(/3 other selected item\(s\) are not on this page/),
    ).toBeVisible()
  })

  // Read off the rendered dialog rather than a table of letters: a table can
  // agree with itself while a control is left without its attribute.
  it('gives the dialog unique, unreserved access keys', async () => {
    renderDialog()
    const dialog = await screen.findByRole('dialog')
    const letters = [...dialog.querySelectorAll('[accesskey]')].map((el) =>
      el.getAttribute('accesskey'),
    )
    expect(letters).toHaveLength(3)
    expect(new Set(letters).size).toBe(letters.length)
    // Chrome and Edge keep D, E and F for the address bar and menus.
    expect(letters.filter((l) => 'def'.includes(l))).toEqual([])
  })

  it('offers with Ctrl+S', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillIn(user)
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })

    await waitFor(() =>
      expect(api.createOffers).toHaveBeenCalledWith({
        venue: 'ebay',
        format: 'fixed_price',
        items: [
          {
            item_id: 7,
            price: '189.00',
            title: '1881-S Morgan Dollar',
            description: 'Blast white.',
            external_id: null,
          },
          {
            item_id: 9,
            price: '99.00',
            title: '1923 Peace Dollar',
            description: 'Lightly toned.',
            external_id: null,
          },
        ],
      }),
    )
  })

  // In StrictMode, which is how the console really runs (`management/main.jsx`),
  // React runs every effect setup, cleanup, setup on mount. A "still mounted?"
  // guard armed once at useRef(true) is left false by that first cleanup, and
  // an offer the API accepted would never reach the parent: the dialog would
  // sit on "Offering..." with the items already listed on eBay.
  it('applies a successful offer in StrictMode, where effects run twice', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    renderDialog({ onOffered }, { strict: true })
    await fillIn(user)
    await user.click(offerButton())

    await waitFor(() => expect(onOffered).toHaveBeenCalledWith([LISTING]))
  })

  it('does not apply an offer the user cancelled before it resolved', async () => {
    const user = userEvent.setup()
    const onOffered = vi.fn()
    let resolveOffer
    api.createOffers.mockReturnValue(
      new Promise((resolve) => {
        resolveOffer = resolve
      }),
    )

    function Harness() {
      const [open, setOpen] = useState(true)
      return open ? (
        <OfferDialog
          items={ITEMS}
          skipped={0}
          onOffered={onOffered}
          onClose={() => setOpen(false)}
        />
      ) : null
    }

    render(<Harness />)
    await fillIn(user)
    await user.click(offerButton())
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    await act(async () => {
      resolveOffer({ listings: [LISTING] })
    })
    expect(onOffered).not.toHaveBeenCalled()
  })
})

// The arithmetic itself, against its own table of cases. Whole cents
// throughout: a float cannot hold them, and these numbers decide what the
// owner thinks a sale is worth.
describe('platform fees', () => {
  const ebay = VENUES[1]
  const store = VENUES[0]
  //: A platform charging a flat fee per listing and nothing else.
  const flat = { commission_rate: null, processing_rate: null, listing_fee: '0.35' }

  it('knows which platforms have a recorded fee', () => {
    expect(hasDefaultFees(ebay)).toBe(true)
    expect(hasDefaultFees(store)).toBe(false)
    expect(hasDefaultFees(null)).toBe(false)
    // A recorded zero is a fact: this platform charges nothing.
    expect(hasDefaultFees({ commission_rate: '0.0000' })).toBe(true)
  })

  it.each([
    ['189.00', ebay, '30.82'],
    ['100.00', ebay, '16.45'],
    ['10.00', flat, '0.35'],
    // No recorded fees is not a fee of zero.
    ['189.00', store, ''],
    ['', ebay, ''],
    [null, ebay, ''],
  ])('fees on %s are %s', (price, venue, expected) => {
    expect(estimatedFees(price, venue)).toBe(expected)
  })

  it.each([
    ['189.00', ebay, '158.18'],
    ['100.00', ebay, '83.55'],
    // Priced below the platform's own fixed fee: the net is negative, and
    // saying so is the point.
    ['0.10', flat, '-0.25'],
    ['189.00', store, ''],
  ])('what is left of %s is %s', (price, venue, expected) => {
    expect(netAfterFees(price, venue)).toBe(expected)
  })

  it.each([
    ['189.00', '120.00', ebay, '20.2'],
    // The same sale with no platform taking a cut.
    ['189.00', '120.00', store, '36.5'],
    ['189.00', '120.00', null, '36.5'],
    // Unknowable, and therefore blank rather than zero.
    ['189.00', null, ebay, ''],
    ['0.00', '10.00', ebay, ''],
  ])('margin on %s costing %s is %s', (price, cost, venue, expected) => {
    expect(netMarginPercent(price, cost, venue)).toBe(expected)
  })
})
