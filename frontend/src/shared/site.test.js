import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_SHOP_NAME, siteFrom } from './site'

afterEach(() => vi.unstubAllEnvs())

describe('siteFrom', () => {
  it('takes the name and owner from the settings', () => {
    expect(
      siteFrom({ VITE_SHOP_NAME: 'Example Mint', VITE_SHOP_OWNER: 'Example Mint LLC' }),
    ).toEqual({ shopName: 'Example Mint', shopOwner: 'Example Mint LLC' })
  })

  it.each([
    ['nothing set', {}],
    ['blank settings', { VITE_SHOP_NAME: '  ', VITE_SHOP_OWNER: '' }],
  ])('is a neutral name and no owner for %s', (_case, env) => {
    expect(siteFrom(env)).toEqual({ shopName: DEFAULT_SHOP_NAME, shopOwner: '' })
    expect(DEFAULT_SHOP_NAME).toBe('Coin Shop')
  })
})

describe('SHOP_NAME and SHOP_OWNER', () => {
  it("are the build's settings", async () => {
    vi.stubEnv('VITE_SHOP_NAME', 'Example Mint')
    vi.stubEnv('VITE_SHOP_OWNER', 'Example Mint LLC')
    vi.resetModules()
    const site = await import('./site')
    expect(site.SHOP_NAME).toBe('Example Mint')
    expect(site.SHOP_OWNER).toBe('Example Mint LLC')
  })
})
