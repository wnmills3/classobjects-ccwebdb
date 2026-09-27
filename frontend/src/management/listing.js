//: Where each marketplace puts the listing's own id in its web address. An
//: order page (Whatnot's `/order/`, eBay's order.ebay.com) names an order,
//: not a listing, and has none.
const LISTING_IDS = [
  /ebay\.[a-z.]+\/itm\/(?:[^/?#]*\/)?(\d{9,15})/i,
  /hibid\.com\/lot\/(\d+)/i,
  /liveauctioneers\.com\/item\/(\d+)/i,
  /proxibid\.com\/lotinformation\/(\d+)/i,
]

/**
 * The seller's own id for a listing, read from its web address, or null.
 *
 * eBay's item number, a HiBid, LiveAuctioneers or Proxibid lot -- the value
 * the New item form suggests for "Seller's item id".
 */
export function listingIdFrom(url) {
  for (const pattern of LISTING_IDS) {
    const found = pattern.exec(url ?? '')
    if (found) return found[1]
  }
  return null
}
