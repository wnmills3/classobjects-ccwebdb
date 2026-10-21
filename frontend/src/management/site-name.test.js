import { describe, expect, it } from 'vitest'

import { siteName, withScheme } from './site-name'

describe('withScheme', () => {
  it.each([
    // A site typed without one: https:// in front, the rest as typed.
    ['www.apmex.com', 'https://www.apmex.com'],
    ['  hibid.com/lots  ', 'https://hibid.com/lots'],
    ['WWW.APMEX.com', 'https://WWW.APMEX.com'],
    // It has one already.
    [
      'https://www.ebay.com/usr/example_seller',
      'https://www.ebay.com/usr/example_seller',
    ],
    ['http://usmint.gov', 'http://usmint.gov'],
    // A mailbox is not a site.
    ['coins@example.com', 'coins@example.com'],
    ['mailto:coins@example.com', 'mailto:coins@example.com'],
    // Not a site: sent as typed, for the server to refuse by name.
    ['Gift', 'Gift'],
  ])('%s -> %s', (typed, sent) => {
    expect(withScheme(typed)).toBe(sent)
  })

  it.each([[''], ['   '], [null], [undefined]])('%s is no address', (typed) => {
    expect(withScheme(typed)).toBeNull()
  })
})

describe('siteName', () => {
  it.each([
    // A site of their own under the marketplace's: its first part.
    ['https://exampleauctions.hibid.com/lots', 'exampleauctions'],
    ['https://coinhound.hibid.com/', 'coinhound'],
    // A page on the marketplace's site: its last part, as written.
    ['https://www.ebay.com/usr/coinhound42', 'coinhound42'],
    ['https://www.ebay.com/str/examplecoins/', 'examplecoins'],
    ['https://www.etsy.com/shop/ExampleCoinShop?ref=view_receipt', 'ExampleCoinShop'],
    ['https://www.whatnot.com/user/example_seller#shop', 'example_seller'],
    // Only a site: the site, without www.
    ['https://usmint.gov/', 'usmint.gov'],
    ['WWW.APMEX.com', 'apmex.com'],
    ['hibid.com', 'hibid.com'],
    // A country's two-part ending (`co.uk`) is one ending, not a part of
    // the name: the marketplace's site there is still only a site.
    ['https://www.ebay.co.uk/usr/bob', 'bob'],
    ['https://www.ebay.com.au/str/examplecoins', 'examplecoins'],
    ['examplecoins.co.uk', 'examplecoins.co.uk'],
    ['https://examplehouse.hibid.co.uk/lots', 'examplehouse'],
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
