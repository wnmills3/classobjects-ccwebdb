/**
 * Who runs this site: the shop's name and the legal name its copyright
 * line names, for both applications.
 *
 * Both are settings read when the frontend is built -- VITE_SHOP_NAME and
 * VITE_SHOP_OWNER in the repository's .env -- so an installation brands the
 * shop without editing code. The defaults live here and nowhere else;
 * `vite.config.js` imports this module to put the same name into the HTML
 * page titles.
 */

/** The name a shop that sets none goes by. */
export const DEFAULT_SHOP_NAME = 'Coin Shop'

/**
 * The site's `{ shopName, shopOwner }` from a set of build settings. A
 * blank setting is the same as none: the default name, and no owner.
 */
export function siteFrom(env = {}) {
  const name = String(env.VITE_SHOP_NAME ?? '').trim()
  const owner = String(env.VITE_SHOP_OWNER ?? '').trim()
  return { shopName: name || DEFAULT_SHOP_NAME, shopOwner: owner }
}

// `import.meta.env` is undefined when vite.config.js loads this module in
// Node; only the bundled page has it.
const site = siteFrom(import.meta.env ?? {})

/** The shop's name, shown in the header, the page titles and the footer. */
export const SHOP_NAME = site.shopName

/**
 * The legal name the copyright line names, or '' for none: the footer then
 * shows the shop's name with no copyright holder.
 */
export const SHOP_OWNER = site.shopOwner
