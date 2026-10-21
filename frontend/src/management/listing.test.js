import { describe, expect, it } from 'vitest'

import { listingIdFrom } from './listing'

describe('listingIdFrom', () => {
  it.each([
    ['https://www.ebay.com/itm/434343434343', '434343434343'],
    ['https://www.ebay.com/itm/1878-Morgan-Dollar/444444444444?hash=x', '444444444444'],
    [
      'https://hibid.com/lot/280623476/1986-2024-american-eagle--5-00-gold-coin',
      '280623476',
    ],
    [
      'https://exampleauctions.hibid.com/lot/218761505/-3--2006-silver-eagle',
      '218761505',
    ],
    [
      'https://www.liveauctioneers.com/item/194045322_1880-s-1-morgan-silver-dollar',
      '194045322',
    ],
    [
      'https://www.proxibid.com/lotinformation/91896919/buffalo-nickel-roll#Top',
      '91896919',
    ],
    ['https://www.proxibid.com/lotInformation/91896919/x', '91896919'],
  ])('reads the listing id from %s', (url, id) => {
    expect(listingIdFrom(url)).toBe(id)
  })

  it.each([
    // Order pages name an order, not a listing.
    'https://www.whatnot.com/order/YLMRqwXPqcdsXx35UxxA2V',
    'https://order.ebay.com/ord/show?orderId=12-34567-89012',
    'https://www.usmint.gov/coins/some-product',
    'not a link at all',
    '',
  ])('finds none in %s', (url) => {
    expect(listingIdFrom(url)).toBeNull()
  })
})
