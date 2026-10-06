import { describe, expect, it } from 'vitest'

import { siteName } from './site-name'

describe('siteName', () => {
  it.each([
    // A site of their own under the marketplace's: its first part.
    ['https://exampleauctions.hibid.com/lots', 'exampleauctions'],
    ['https://memauctionhouse.hibid.com/', 'memauctionhouse'],
    // A page on the marketplace's site: its last part, as written.
    ['https://www.ebay.com/usr/coinbuyer42', 'coinbuyer42'],
    ['https://www.ebay.com/str/examplecoins/', 'examplecoins'],
    [
      'https://www.etsy.com/shop/ExampleCoinShop?ref=view_receipt',
      'ExampleCoinShop',
    ],
    ['https://www.whatnot.com/user/example_seller#shop', 'example_seller'],
    // Only a site: the site, without www.
    ['https://usmint.gov/', 'usmint.gov'],
    ['WWW.APMEX.com', 'apmex.com'],
    ['hibid.com', 'hibid.com'],
  ])('%s -> %s', (address, name) => {
    expect(siteName(address)).toBe(name)
  })

  it.each([
    [''],
    [null],
    ['https://www'],
    ['not an address'],
    // A mailbox, not a place.
    ['coins@example.com'],
    ['mailto:coins@example.com'],
    // Not decodable: nothing to propose, and nothing thrown.
    ['https://www.ebay.com/usr/%E0%A4%A'],
  ])('%s proposes nothing', (address) => {
    expect(siteName(address)).toBe('')
  })
})
