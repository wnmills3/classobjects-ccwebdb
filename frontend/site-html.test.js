import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import { fillShopName, siteHtml } from './site-html.js'

// Read from the frontend folder, where the test runner starts.
const page = (name) => readFileSync(name, 'utf-8')

describe("the pages' titles carry the shop's name", () => {
  it.each([
    ['index.html', 'Example Mint - Coins &amp; Currency'],
    ['management.html', 'Example Mint console'],
  ])('%s takes it from the settings', (name, title) => {
    const html = fillShopName(page(name), { VITE_SHOP_NAME: 'Example Mint' })
    expect(html).toContain(`<title>${title}</title>`)
  })

  it('falls back to the neutral name, leaving no placeholder', () => {
    const html = fillShopName(page('index.html'), {})
    expect(html).toContain('<title>Coin Shop - Coins &amp; Currency</title>')
    expect(html).not.toContain('%VITE_SHOP_NAME%')
  })

  it('escapes the name, which is free text', () => {
    const html = fillShopName('<title>%VITE_SHOP_NAME%</title>', {
      VITE_SHOP_NAME: 'Coins <&> "Notes"',
    })
    expect(html).toBe('<title>Coins &lt;&amp;&gt; &quot;Notes&quot;</title>')
  })

  it("runs before Vite's own placeholder pass", () => {
    const plugin = siteHtml({ VITE_SHOP_NAME: 'Example Mint' })
    expect(plugin.transformIndexHtml.order).toBe('pre')
    expect(plugin.transformIndexHtml.handler('%VITE_SHOP_NAME%')).toBe('Example Mint')
  })
})
