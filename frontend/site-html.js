import { siteFrom } from './src/shared/site.js'

/**
 * The shop's name in the HTML pages' titles.
 *
 * The pages write `%VITE_SHOP_NAME%`. Vite fills such a placeholder from the
 * settings itself, but leaves it as written when the setting is absent; this
 * fills it first, with the same default the pages' code uses, and escaped,
 * since a name is free text and a title is HTML.
 *
 * Its own module, not vite.config.js, so a test can import it (see
 * seal-warning.js).
 */

const PLACEHOLDER = '%VITE_SHOP_NAME%'

/** `text` safe to put in HTML. */
const escapeHtml = (text) =>
  text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')

/** `html` with the shop's name, from the settings `env`, in its placeholders. */
export function fillShopName(html, env) {
  return html.replaceAll(PLACEHOLDER, escapeHtml(siteFrom(env).shopName))
}

/**
 * The Vite plugin that does it, for both pages. `order: 'pre'` runs it
 * before Vite's own placeholder pass, which would otherwise warn about an
 * unset setting and leave the placeholder in the title.
 */
export function siteHtml(env) {
  return {
    name: 'ccwebdb-site-html',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => fillShopName(html, env),
    },
  }
}
