/**
 * The name a web address proposes for whoever it belongs to: the part of the
 * address that is theirs. The vendor and seller forms ask for the address
 * first and offer this as the name.
 *
 * Someone who sells has a place of their own on a marketplace, and the
 * address says where, in one of two ways:
 *
 * - a site of their own under the marketplace's -- `goldstandardauctions` of
 *   `https://goldstandardauctions.hibid.com/lots`: the first part of the
 *   site, whatever page follows;
 * - a page on the marketplace's site -- `drh9989` of
 *   `https://www.ebay.com/usr/drh9989`: the last part of the page, written
 *   as the address writes it.
 *
 * An address that is only a site is named for it, without `www.`:
 * `usmint.gov`. What follows `?` or `#` is never part of a name. Empty until
 * the text is a web address, and for a mail address, which names a mailbox
 * and not a place.
 */
export function siteName(text) {
  const typed = (text ?? '').trim()
  if (/^mailto:/i.test(typed) || typed.includes('@')) return ''
  // A site has a dot and no spaces; anything less is still being typed.
  if (!/^[^\s]+\.[^\s.]/.test(typed.replace(/^[a-z]+:\/\//i, ''))) return ''
  try {
    const url = new URL(/^[a-z]+:\/\//i.test(typed) ? typed : `https://${typed}`)
    const site = url.hostname.toLowerCase().replace(/^www\./, '')
    const parts = site.split('.')
    // More than a name and its ending: a site of their own.
    if (parts.length > 2) return parts[0]
    const last = url.pathname.split('/').filter(Boolean).pop()
    return last ? decodeURIComponent(last) : site
  } catch {
    return ''
  }
}
