import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Link, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Only the method the page is supposed to call. That is deliberate: the page
// called `api.getCoin`, which does not exist on the real client, so every
// visit threw `TypeError: api.getCoin is not a function` before the page
// could render. A mock listing every method would have hidden that; this one
// fails the same way production did.
vi.mock('../../shared/api', () => ({ api: { getCatalogItem: vi.fn() } }))

import { api } from '../../shared/api'
import CoinDetail from './CoinDetail'
import { emptyCart, emptyReference, renderWithProviders } from '../../test/helpers'

beforeEach(() => vi.clearAllMocks())

const COIN = {
  id: 7,
  title: 'Morgan Dollar 1921',
  price: '89.5',
  quantity_available: 2,
  item_code: 'CC-000007',
  grade_display: 'MS64',
  description: 'A lightly toned example.',
}

const show = (options = {}) =>
  renderWithProviders(
    <Routes>
      <Route path="/coin/:id" element={<CoinDetail />} />
    </Routes>,
    { route: '/coin/7', ...options },
  )

describe('CoinDetail', () => {
  it('loads the item named in the URL', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    show()

    expect(await screen.findByText('Morgan Dollar 1921')).toBeInTheDocument()
    // The id from the path, not a default: a page that ignored it would show
    // the right-looking coin for every URL.
    expect(api.getCatalogItem).toHaveBeenCalledWith('7')
  })

  it('shows the price, stock and specifications', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    show()

    expect(await screen.findByText('$89.50')).toBeInTheDocument()
    expect(screen.getByText('2 available')).toBeInTheDocument()
    expect(screen.getByText('CC-000007')).toBeInTheDocument()
    expect(screen.getByText('MS64')).toBeInTheDocument()
  })

  it('adds the coin to the cart', async () => {
    api.getCatalogItem.mockResolvedValue(COIN)
    const cart = emptyCart()
    show({ cart })

    const button = await screen.findByRole('button', { name: 'Add to cart' })
    button.click()
    expect(cart.add).toHaveBeenCalledWith(COIN, 1)
  })

  it('refuses to add a sold-out coin', async () => {
    api.getCatalogItem.mockResolvedValue({ ...COIN, quantity_available: 0 })
    show()

    const button = await screen.findByRole('button', { name: 'Sold out' })
    expect(button).toBeDisabled()
  })

  it('reports a failed load rather than showing nothing', async () => {
    api.getCatalogItem.mockRejectedValue(new Error('that coin is not for sale'))
    show()
    expect(await screen.findByText('that coin is not for sale')).toBeInTheDocument()
  })

  it('names the type by its label, not its code', async () => {
    api.getCatalogItem.mockResolvedValue({ ...COIN, item_kind: 'bullion' })
    show({
      reference: emptyReference({
        tables: { item_kind: [{ code: 'bullion', label: 'Bullion' }] },
      }),
    })
    expect(await screen.findByText('Bullion')).toBeInTheDocument()
    expect(screen.queryByText('bullion')).toBeNull()
  })

  it('never shows the previous entry while the next one loads', async () => {
    // A link from one entry to another changes the id without remounting
    // the page; the first coin stayed on screen, under the second's URL,
    // until the second arrived.
    const user = userEvent.setup()
    let arrive
    api.getCatalogItem.mockImplementation((id) =>
      id === '7'
        ? Promise.resolve(COIN)
        : new Promise((resolve) => {
            arrive = resolve
          }),
    )
    renderWithProviders(
      <>
        <Link to="/coin/8">next</Link>
        <Routes>
          <Route path="/coin/:id" element={<CoinDetail />} />
        </Routes>
      </>,
      { route: '/coin/7' },
    )
    expect(await screen.findByText('Morgan Dollar 1921')).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'next' }))

    expect(screen.queryByText('Morgan Dollar 1921')).toBeNull()
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    arrive({ ...COIN, id: 8, title: 'Peace Dollar 1922' })
    expect(await screen.findByText('Peace Dollar 1922')).toBeInTheDocument()
  })
})

// A LOT: a group of coins sold as one thing. Every item-describing field is
// null -- no single kind, grade, metal or year describes a group -- and so is
// every photograph, because a lot has none of its own. `members` is the whole
// description, and the page read none of it.
const LOT = {
  id: 12,
  title: 'Three Morgan Dollars, 1881-1883',
  description: 'A short date run.',
  price: '1200.00',
  quantity_available: 1,
  inventory_item_id: null,
  item_code: null,
  item_kind: null,
  country: null,
  year_start: null,
  grade_display: null,
  thumbnail_url: null,
  image_url: null,
  piece_count: 3,
  members: [
    {
      inventory_item_id: 7,
      item_code: 'CC-000007',
      title: '1881-S Morgan Dollar',
      description: 'Blast white.',
      country: 'US',
      year_start: 1881,
      denomination: 'dollar',
      grade_display: 'MS64',
      piece_count: 1,
      thumbnail_url: '/media/thumb/aaa.jpg',
      image_url: '/media/web/aaa.jpg',
    },
    {
      inventory_item_id: 9,
      item_code: 'CC-000009',
      title: '1882-S Morgan Dollar',
      description: '',
      country: 'US',
      year_start: 1882,
      denomination: 'dollar',
      grade_display: 'MS63',
      piece_count: 1,
      thumbnail_url: null,
      image_url: null,
    },
    {
      inventory_item_id: 11,
      item_code: 'CC-000011',
      title: '1883-O Morgan Dollar',
      description: '',
      country: 'US',
      year_start: 1883,
      denomination: 'dollar',
      grade_display: 'MS62',
      piece_count: 1,
      thumbnail_url: null,
      image_url: null,
    },
  ],
}

describe('a lot in the shop', () => {
  it('lists the coins in a lot', async () => {
    // Every FIELDS key above is null for a lot, so the specifications table
    // renders nothing at all: without the members, the page is a title, a
    // price, and no description of what is being sold.
    api.getCatalogItem.mockResolvedValue(LOT)
    show()

    expect(await screen.findByText('1881-S Morgan Dollar')).toBeInTheDocument()
    expect(screen.getByText('1882-S Morgan Dollar')).toBeInTheDocument()
    expect(screen.getByText('1883-O Morgan Dollar')).toBeInTheDocument()
    expect(screen.getByText('US - 1881 - dollar - MS64')).toBeInTheDocument()
  })

  it('says how many coins are in it, so the price is not read as one coin’s', async () => {
    api.getCatalogItem.mockResolvedValue(LOT)
    show()
    expect(await screen.findByText('Lot of 3 items')).toBeInTheDocument()
  })

  it('counts the pieces as well when a member is more than one object', async () => {
    // `piece_count` is summed over the members and a member may itself be a
    // roll or a mint set. Three entries and twenty-two objects are both true
    // and saying only one of them is how the wrong parcel is expected.
    api.getCatalogItem.mockResolvedValue({ ...LOT, piece_count: 22 })
    show()
    expect(await screen.findByText('Lot of 3 items, 22 pieces in all')).toBeVisible()
  })

  it('shows a photograph of the lot, taken from the coin that has one', async () => {
    // A lot's own `image_url` is null by design -- "its members carry theirs"
    // -- so the page showed no picture at all for something being sold for
    // 1200.00. The alt text says which coin it is rather than passing it off
    // as a photograph of the group.
    api.getCatalogItem.mockResolvedValue(LOT)
    show()

    const picture = await screen.findByRole('img', {
      name: '1881-S Morgan Dollar, one of the 3 items in this lot',
    })
    expect(picture).toHaveAttribute('src', '/media/web/aaa.jpg')
  })

  it('still lists the coins a sold lot held', async () => {
    // The detail endpoint serves an ended listing on purpose, so a page
    // someone bookmarked can say the offer is over. `_lot_entry` reads
    // `members_held`, the past-tense question, so the list is FULL after a
    // sale -- `offered_items` would have answered "none" for exactly the
    // page a buyer is most likely to be looking at. A page that hid the
    // members once the lot was sold would throw that away again.
    api.getCatalogItem.mockResolvedValue({
      ...LOT,
      quantity_available: 0,
      is_active: false,
    })
    show()

    expect(await screen.findByText('1881-S Morgan Dollar')).toBeInTheDocument()
    expect(screen.getByText('Lot of 3 items')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sold out' })).toBeDisabled()
  })

  it('leaves a single coin alone', async () => {
    // The other side of the branch: a coin must not sprout a "what is in
    // this lot" section, and its own photograph must still be its own.
    api.getCatalogItem.mockResolvedValue({
      ...COIN,
      image_url: '/media/web/bbb.jpg',
      members: [],
    })
    show()

    expect(await screen.findByText('CC-000007')).toBeInTheDocument()
    expect(screen.queryByText(/what is in this lot/i)).toBeNull()
    expect(screen.queryByText(/^Lot of/)).toBeNull()
    expect(screen.getByRole('img', { name: 'Morgan Dollar 1921' })).toHaveAttribute(
      'src',
      '/media/web/bbb.jpg',
    )
  })
})
