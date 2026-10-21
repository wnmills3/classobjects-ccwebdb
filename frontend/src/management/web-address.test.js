import { describe, expect, it } from 'vitest'

import { isWebAddress } from './web-address'

describe('isWebAddress', () => {
  it.each([
    'https://www.example.com/itm/1',
    'http://example.com',
    'HTTPS://EXAMPLE.COM',
  ])('takes %s', (text) => expect(isWebAddress(text)).toBe(true))

  it.each([
    'javascript:alert(1)',
    ' https://example.com',
    'data:text/html,hi',
    '//example.com',
    'example.com',
    '',
    null,
    undefined,
  ])('refuses %s', (text) => expect(isWebAddress(text)).toBe(false))
})
