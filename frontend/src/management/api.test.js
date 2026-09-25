/**
 * The console's API client, at the seam the page tests cannot see.
 *
 * Every page test mocks `api` wholesale and asserts the arguments handed to
 * it. That is the right thing for a page, but it means the client's own
 * translation from those arguments into an HTTP body is exercised by
 * nothing -- and that translation is where a request can quietly come to
 * mean something else. These tests read the body actually sent.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { saveTokens } from '../shared/api'

function captureFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    status: 200,
    ok: true,
    text: () => Promise.resolve('{}'),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The parsed JSON body of the one request that was made. */
function sentBody(fetchMock) {
  return JSON.parse(fetchMock.mock.calls[0][1].body)
}

afterEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('updateImageLink', () => {
  // The endpoint distinguishes an omitted field from an explicit null, so
  // what is *absent* from the body is as meaningful as what is present.
  it('does not mention the role when only making a photograph primary', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { isPrimary: true })

    const body = sentBody(fetchMock)
    expect(body.is_primary).toBe(true)
    // The bug this covers: `image_role: null` here reads as "clear the
    // role", so promoting a photograph erased its obverse/reverse.
    expect('image_role' in body).toBe(false)
  })

  it('does not mention is_primary when only setting a role', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { imageRole: 'obverse' })

    const body = sentBody(fetchMock)
    expect(body.image_role).toBe('obverse')
    expect('is_primary' in body).toBe(false)
  })

  it('still sends an explicit null to clear a role', async () => {
    // The console's blank option. A truthiness check on `imageRole` would
    // drop this and make that option a no-op again -- which is the bug the
    // endpoint's `model_fields_set` was introduced to fix.
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { imageRole: null })

    const body = sentBody(fetchMock)
    expect('image_role' in body).toBe(true)
    expect(body.image_role).toBeNull()
  })

  it('always carries the for-sale acknowledgement', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { isPrimary: true, acknowledgeForSale: true })

    expect(sentBody(fetchMock).acknowledge_for_sale).toBe(true)
  })
})

describe('recordSale', () => {
  // RecordSaleIn validates price and every fee amount as a Decimal string --
  // `ge=0, max_digits=12, decimal_places=2`. A `Number` round-trip anywhere
  // between the dialog and this call is how a cent goes missing or a value
  // the schema refuses gets sent, so this reads the literal JSON body rather
  // than trusting that what went in comes out unchanged.
  it('posts the money fields as the exact strings given, to the sale endpoint', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    // Three different numbers throughout, on purpose: a fixture where gross,
    // one fee and price coincide could pass a body assertion for the wrong
    // reason -- one value standing in for another.
    await api.recordSale(14, {
      price: '115.00',
      buyer_username: 'coinfan88',
      external_order_id: '04-12345-67890',
      fees: [{ kind: 'commission', amount: '20.35' }],
      equal_shares: false,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/listings/14/sale',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = sentBody(fetchMock)
    expect(body).toEqual({
      price: '115.00',
      buyer_username: 'coinfan88',
      external_order_id: '04-12345-67890',
      fees: [{ kind: 'commission', amount: '20.35' }],
      equal_shares: false,
    })
    // Strings, not numbers: `JSON.parse` would turn "115.00" into 115 if the
    // client had converted it, silently dropping the trailing zero the
    // schema's `decimal_places=2` requires on the wire.
    expect(typeof body.price).toBe('string')
    expect(typeof body.fees[0].amount).toBe('string')
  })

  it('sends a blank buyer and order id as null, not as an empty string', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.recordSale(14, {
      price: '115.00',
      buyer_username: null,
      external_order_id: null,
      fees: [],
      equal_shares: false,
    })

    const body = sentBody(fetchMock)
    expect(body.buyer_username).toBeNull()
    expect(body.external_order_id).toBeNull()
    expect(body.fees).toEqual([])
  })
})

describe('auctions', () => {
  // `AuctionLotIn` and `SettleIn` validate every money field as a Decimal
  // string -- reserve, price, hammer price, a fee amount -- the same shape
  // `recordSale` above is tested against, and for the same reason: a
  // `Number` round-trip anywhere between the page and this call is how a
  // cent goes missing.
  it('adds a lot with its money fields as the exact strings given', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.addAuctionLot(1, {
      lot_number: '1',
      item_id: 42,
      reserve: '20.00',
      price: '10.00',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auctions/1/lots',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = sentBody(fetchMock)
    expect(body).toEqual({
      lot_number: '1',
      item_id: 42,
      reserve: '20.00',
      price: '10.00',
    })
    expect(typeof body.reserve).toBe('string')
    expect(typeof body.price).toBe('string')
  })

  it('removes a lot with the return location as a query parameter', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.removeAuctionLot(1, 7, 9)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auctions/1/lots/7?returned_to_location_id=9')
    expect(init.method).toBe('DELETE')
  })

  it('removes a lot with no query parameter when no location is given', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.removeAuctionLot(1, 7, null)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/auctions/1/lots/7')
  })

  it('cancels an auction with an empty body when it was never consigned', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.cancelAuction(1)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auctions/1/cancel')
    expect(JSON.parse(init.body)).toEqual({})
  })

  it('settles an auction with the hammer prices and fees as the exact strings typed', async () => {
    // Three different figures throughout, on purpose (as `recordSale`'s own
    // test above does): a fixture where the hammer price and a fee coincide
    // could pass for the wrong reason.
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.settleAuction(1, {
      lines: [
        {
          auction_lot_id: 201,
          result: 'sold',
          hammer_price: '150.00',
          buyer_username: 'amy',
        },
        {
          auction_lot_id: 202,
          result: 'unsold',
          hammer_price: null,
          buyer_username: null,
        },
      ],
      fees: [
        { buyer_username: 'amy', fees: [{ kind: 'commission', amount: '24.00' }] },
      ],
    })

    const body = sentBody(fetchMock)
    expect(body.lines[0].hammer_price).toBe('150.00')
    expect(typeof body.lines[0].hammer_price).toBe('string')
    expect(body.fees[0].fees[0].amount).toBe('24.00')
    expect(typeof body.fees[0].fees[0].amount).toBe('string')
  })
})

describe('sales lots', () => {
  it('sends lot membership changes as the API expects', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateLot(1, { version: 3, add_item_ids: [7] })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/sales-lots/1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ version: 3, add_item_ids: [7] })
  })

  it('starts a lot with its wording alone', async () => {
    // `SalesLotIn` is `extra="forbid"` and holds only `title` and
    // `description`: "a lot begins assembling and empty; members are a
    // PATCH". A `createLot` that passed membership through would be a 422
    // every time, so what this asserts is the *absence* of it.
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.createLot({ title: 'Three Morgans', description: 'A run.' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/sales-lots')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({
      title: 'Three Morgans',
      description: 'A run.',
    })
  })

  it('asks for one status without sending the empty ones', async () => {
    // A blank filter must not arrive as `status=`: the endpoint resolves
    // whatever it is given against `SalesLotStatus` and answers 422 for an
    // empty string, so a page whose filter starts blank would fail to load.
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.listLots({ status: 'assembling' })
    await api.listLots({ status: '' })

    expect(fetchMock.mock.calls[0][0]).toBe('/api/sales-lots?status=assembling')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sales-lots')
  })
})

describe('getOfferTitles', () => {
  // FastAPI reads a list query parameter from a repeated key; a single
  // comma-joined value would be one unparseable id and a 422.
  it('repeats item_ids once per item', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.getOfferTitles([7, 9])

    expect(fetchMock.mock.calls[0][0]).toBe('/api/offers/titles?item_ids=7&item_ids=9')
  })
})
