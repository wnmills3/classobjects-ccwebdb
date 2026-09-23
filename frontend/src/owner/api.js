/**
 * The console's API calls, kept out of the shop's bundle.
 *
 * `shared/api.js` is a single object literal, so nothing tree-shakes out of
 * it: every path written there is downloaded by every anonymous visitor to
 * the shop. A list of `/api/inventory/...`, `/api/users/...` and
 * `/api/customers/...` endpoints is a map of the owner's tooling -- the same
 * information the stylesheet split removed, and removed for the same reason.
 *
 * This is not the access control. `require_admin` on the backend is, and it
 * guards every one of these endpoints whether or not the path is guessable.
 * Keeping the map out of the shop is defence in depth.
 *
 * Exported as `api` spreading the shared object, so a console page imports one
 * `api` and calls shop and console methods alike without knowing which is
 * which. The split is a bundling concern, not something callers should think
 * about.
 */
import { api as shared, send } from '../shared/api'

/** A query string from the parameters that have a value. */
function query(params) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) qs.set(k, v)
  })
  return qs.toString()
}

export const api = {
  ...shared,

  // No catalogue writes: the Manage page that used them is retired, and
  // offering an item the business already owns is what replaces it
  // (`docs/plans/selling-offers.md`). The endpoints themselves go with that
  // work, so nothing loses the ability to end a listing meanwhile.

  renameReferenceValue: (table, code, payload) =>
    send(`/api/reference/${table}/${code}`, { method: 'PATCH', body: payload }),
  // Retired values and removed aliases included, for the Vocabularies page.
  getReferenceForEditing: (table) =>
    send(`/api/reference/${table}?${query({ include_inactive: true })}`),
  // dryRun: say what would move, change nothing. acknowledgeForSale: the
  // operator has seen the preview's for-sale items and confirmed the merge
  // should move them anyway.
  mergeReferenceValue: (
    table,
    code,
    into,
    dryRun,
    { acknowledgeForSale = false } = {},
  ) =>
    send(`/api/reference/${table}/${encodeURIComponent(code)}/merge`, {
      method: 'POST',
      body: { into, dry_run: dryRun, acknowledge_for_sale: acknowledgeForSale },
    }),
  addReferenceAlias: (table, code, alias) =>
    send(`/api/reference/${table}/${encodeURIComponent(code)}/aliases`, {
      method: 'POST',
      body: { alias },
    }),
  removeReferenceAlias: (table, code, alias) =>
    send(
      `/api/reference/${table}/${encodeURIComponent(code)}/aliases?${query({ alias })}`,
      { method: 'DELETE' },
    ),

  // inventory
  searchInventory: (view, params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined && v !== false) qs.set(k, v)
    })
    const query = qs.toString()
    const suffix = query ? `?${query}` : ''
    return send(`/api/inventory/${view}/search${suffix}`)
  },
  splitItem: (itemId, payload) =>
    send(`/api/inventory/${itemId}/split`, { method: 'POST', body: payload }),
  getInventoryItem: (id) => send(`/api/inventory/${id}`),
  // Every sale of an item, each with the item as it was sold.
  getItemSales: (id) => send(`/api/inventory/${id}/sales`),
  updateInventoryItem: (id, payload) =>
    send(`/api/inventory/${id}`, { method: 'PATCH', body: payload }),
  bulkEditInventory: (ids, changes) =>
    send('/api/inventory/bulk', { method: 'POST', body: { ids, changes } }),
  setItemReview: (id, fields, replace = false) =>
    send(`/api/inventory/${id}/reviewed`, {
      method: 'POST',
      body: { fields, replace },
    }),
  detachInventoryItem: (id) =>
    send(`/api/inventory/${id}/parent`, { method: 'DELETE' }),
  deleteInventoryItem: (id) => send(`/api/inventory/${id}`, { method: 'DELETE' }),
  // Errors: several per item -- a bill is commonly miscut AND misprinted --
  // each with its own note. PUT replaces the whole set.
  getItemErrors: (id) => send(`/api/inventory/${id}/errors`),
  setItemErrors: (id, errors, { acknowledgeForSale = false } = {}) =>
    send(`/api/inventory/${id}/errors`, {
      method: 'PUT',
      body: { errors, acknowledge_for_sale: acknowledgeForSale },
    }),

  // friedberg -- the owner's own banknote catalogue: searched by what is
  // visible on a note in hand, recorded from a number read off one, and
  // attached to the currency item it identifies. Ships empty by design --
  // CLAUDE.md forbids seeding, fetching or hardcoding a publisher's Friedberg
  // mapping, so nothing here ever does.
  searchFriedberg: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined) qs.set(k, v)
    })
    const query = qs.toString()
    return send(`/api/friedberg${query ? `?${query}` : ''}`)
  },
  createFriedbergNumber: (payload) =>
    send('/api/friedberg', { method: 'POST', body: payload }),
  attachFriedberg: (itemId, payload) =>
    send(`/api/inventory/${itemId}/friedberg`, { method: 'POST', body: payload }),
  // Takes the number off the note; the catalogue row stays.
  clearFriedberg: (itemId) =>
    send(`/api/inventory/${itemId}/friedberg`, { method: 'DELETE' }),
  // Signature combinations narrowed to the pairs whose term covers a series
  // year -- not `getReference` (in shared/api.js), which has no way to pass
  // `year`. Adding the param there would hand every anonymous shop visitor a
  // query string only this console feature has a reason to use.
  getSignatureCombinations: (year) =>
    send(`/api/reference/signature_combination${year ? `?year=${year}` : ''}`, {
      auth: false,
    }),

  // acquisition -- reading what was ordered, to record what arrived
  listPurchaseOrders: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined) qs.set(k, v)
    })
    const query = qs.toString()
    return send(`/api/purchase-orders${query ? `?${query}` : ''}`)
  },
  getPurchaseOrder: (id) => send(`/api/purchase-orders/${id}`),
  listStorageLocations: () => send('/api/storage-locations'),
  receiveItems: (payload) =>
    send('/api/inventory/receive', { method: 'POST', body: payload }),

  // entry -- recording an acquisition that has not been seen anywhere else:
  // a vendor, the purchase made from them, and the items bought on it.
  listVendors: () => send('/api/vendors'),
  createVendor: (payload) => send('/api/vendors', { method: 'POST', body: payload }),

  // sales platforms
  listSalesVenues: () => send('/api/sales-venues'),
  createSalesVenue: (payload) =>
    send('/api/sales-venues', { method: 'POST', body: payload }),
  updateSalesVenue: (code, payload) =>
    send(`/api/sales-venues/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: payload,
    }),

  // offering items for sale. `app/offering_writes.py` is the only writer of a
  // listing's status, so there is no "set this listing inactive" call here:
  // ending an offer is its own endpoint, because ending one also resumes the
  // store listing that was paused for it.
  //
  // A refused batch is a 409 whose body is {detail, refused: [{item_code,
  // reason}]}, and `refused` is always present -- the race case included.
  // `send` throws an ApiError carrying the whole parsed body, not just
  // `detail`, so `OfferDialog` reads `err.body?.refused` to list every
  // refused item with its reason.
  createOffers: (payload) => send('/api/offers', { method: 'POST', body: payload }),
  // A suggested public title per item id, composed from the item's facts
  // (`app/offer_titles.py`) -- what `OfferDialog` pre-fills instead of the
  // seller's `source_title`. Answers {titles: {id: title}}; unknown ids are
  // simply absent.
  getOfferTitles: (itemIds) => {
    const qs = new URLSearchParams()
    itemIds.forEach((id) => qs.append('item_ids', id))
    return send(`/api/offers/titles?${qs}`)
  },
  listListings: (params = {}) => {
    const qs = query(params)
    return send(`/api/listings${qs ? `?${qs}` : ''}`)
  },
  updateListing: (id, payload) =>
    send(`/api/listings/${id}`, { method: 'PATCH', body: payload }),
  endListing: (id) => send(`/api/listings/${id}/end`, { method: 'POST' }),
  // Recording a sale that happened on the platform, with its actual fees.
  // `body`'s money fields are decimal strings already -- see
  // `RecordSaleDialog.jsx` -- and this sends them exactly as given; a
  // `Number` round-trip here is how a cent goes missing.
  recordSale: (listingId, body) =>
    send(`/api/listings/${listingId}/sale`, { method: 'POST', body }),

  // sales lots -- a group of coins sold as one thing. Admin-only: a lot row
  // carries what its coins cost and what they are thought to be worth.
  //
  // There is no "offer this lot" call here: offering a lot is `createOffers`
  // above with `lot_id` instead of `items`, because offering is
  // `offering_writes`' decision and a lot is just another thing to offer.
  //
  // `createLot` takes a title and a description and nothing else --
  // `SalesLotIn` is `extra="forbid"` and a lot "begins assembling and empty;
  // members are a PATCH". A caller that wants a lot with coins in it makes
  // both calls; sending `add_item_ids` here is a 422.
  listLots: (params = {}) => {
    const qs = query(params)
    return send(`/api/sales-lots${qs ? `?${qs}` : ''}`)
  },
  createLot: (body) => send('/api/sales-lots', { method: 'POST', body }),
  // The whole change -- wording and every membership move -- in one body,
  // because the API applies it in one transaction: a screenful of edits
  // either lands or does not. `version` is the version the form loaded.
  updateLot: (id, body) => send(`/api/sales-lots/${id}`, { method: 'PATCH', body }),
  deleteLot: (id) => send(`/api/sales-lots/${id}`, { method: 'DELETE' }),

  // auctions -- a sale event at a platform, its lots and its settlement.
  // Admin-only, and the shapes below match `backend/app/routers/auctions.py`
  // and `backend/app/schemas.py` field for field: every money figure here
  // (reserve, price, hammer_price, a fee amount) is a decimal string on the
  // way in and out, sent exactly as the owner typed it -- never rounded
  // through a JavaScript number.
  //
  // A settlement refusal is a 409 or 422 whose body is
  // `{detail, refused: [{reason, lot_number}]}` (ruling R21), the same shape
  // `createOffers` above documents for `refused`. `send` throws an `ApiError`
  // carrying the whole parsed body, so `SettlementGrid.jsx` reads
  // `err.body?.refused` to mark every offending lot, not just the first.
  listAuctions: (params = {}) => {
    const qs = query(params)
    return send(`/api/auctions${qs ? `?${qs}` : ''}`)
  },
  createAuction: (payload) => send('/api/auctions', { method: 'POST', body: payload }),
  // No `updateAuction` here: this task adds no UI that edits an auction's
  // own title, dates or notes (`AuctionUpdate` in `schemas.py`), and a call
  // nothing sends is dead surface with no request-body test of its own to
  // keep it honest -- add it back with the task that actually needs it.
  scheduleAuction: (id) => send(`/api/auctions/${id}/schedule`, { method: 'POST' }),
  consignAuction: (id, payload) =>
    send(`/api/auctions/${id}/consign`, { method: 'POST', body: payload }),
  closeAuction: (id) => send(`/api/auctions/${id}/close`, { method: 'POST' }),
  // `AuctionCancelIn` takes one optional field, so an auction that was never
  // consigned is cancelled with an empty body.
  cancelAuction: (id, payload = {}) =>
    send(`/api/auctions/${id}/cancel`, { method: 'POST', body: payload }),
  addAuctionLot: (auctionId, payload) =>
    send(`/api/auctions/${auctionId}/lots`, { method: 'POST', body: payload }),
  // `returnedToLocationId` becomes a query parameter, as the endpoint takes
  // it -- required only when the auction is consigned, which the caller
  // decides from `auction.consigned_on`.
  removeAuctionLot: (auctionId, lotId, returnedToLocationId) => {
    const qs = query({ returned_to_location_id: returnedToLocationId ?? null })
    return send(`/api/auctions/${auctionId}/lots/${lotId}${qs ? `?${qs}` : ''}`, {
      method: 'DELETE',
    })
  },
  // Renumbering a lot or changing its reserve. `app.auctions.refuse_unless_lot_editable`
  // refuses this once the auction is closed, settled or cancelled; the page
  // disables the inputs to match, but the refusal is still the real gate.
  updateAuctionLot: (auctionId, lotId, payload) =>
    send(`/api/auctions/${auctionId}/lots/${lotId}`, {
      method: 'PATCH',
      body: payload,
    }),
  // The whole settlement grid, in one call: every lot's result and every
  // buyer's fees, applied or refused together.
  settleAuction: (auctionId, payload) =>
    send(`/api/auctions/${auctionId}/settle`, { method: 'POST', body: payload }),

  createPurchaseOrder: (payload) =>
    send('/api/purchase-orders', { method: 'POST', body: payload }),
  createInventoryItem: (payload) =>
    send('/api/inventory', { method: 'POST', body: payload }),
  // What the facts entered so far decide: a note's class, seal, signatures
  // and Reserve Bank, or a coin's metal. See app/routers/defaults.py.
  suggestNote: (params = {}) => send(`/api/defaults/note?${query(params)}`),
  suggestCoin: (params = {}) => send(`/api/defaults/coin?${query(params)}`),

  // images -- evidence a person looked at the object, attached to an item
  uploadImage: (
    inventoryItemId,
    file,
    { imageRole, isPrimary = false, acknowledgeForSale = false } = {},
  ) => {
    const form = new FormData()
    form.append('file', file)
    form.append('inventory_item_id', String(inventoryItemId))
    if (imageRole) form.append('image_role', imageRole)
    form.append('is_primary', String(isPrimary))
    form.append('acknowledge_for_sale', String(acknowledgeForSale))
    return send('/api/images', { method: 'POST', body: form })
  },
  listItemImages: (inventoryItemId) =>
    send(`/api/images?inventory_item_id=${inventoryItemId}`),
  listUnattachedImages: () => send('/api/images?unattached=true'),
  attachImage: (
    imageId,
    {
      inventoryItemId,
      imageRole,
      isPrimary = false,
      sortOrder = 0,
      acknowledgeForSale = false,
    },
  ) =>
    send(`/api/images/${imageId}/links`, {
      method: 'POST',
      body: {
        inventory_item_id: inventoryItemId,
        image_role: imageRole ?? null,
        is_primary: isPrimary,
        sort_order: sortOrder,
        acknowledge_for_sale: acknowledgeForSale,
      },
    }),
  // Only the fields the caller actually named. The API tells an omitted
  // field from an explicit null (`model_fields_set` in
  // `routers/image_links.py`): omitted leaves the role alone, null clears
  // it. Sending `image_role: imageRole ?? null` erased that distinction, so
  // "make primary" -- which names no role -- arrived as an instruction to
  // clear the role, and promoting a photograph wiped its obverse/reverse.
  //
  // `!== undefined`, not a truthiness check: `setRole(row, null)` is how the
  // console's blank option clears a role, and that null must still be sent.
  updateImageLink: (linkId, { imageRole, isPrimary, acknowledgeForSale = false }) => {
    const body = { acknowledge_for_sale: acknowledgeForSale }
    if (imageRole !== undefined) body.image_role = imageRole
    if (isPrimary !== undefined) body.is_primary = isPrimary
    return send(`/api/image-links/${linkId}`, { method: 'PATCH', body })
  },
  detachImage: (linkId, { acknowledgeForSale = false } = {}) =>
    send(`/api/image-links/${linkId}?acknowledge_for_sale=${acknowledgeForSale}`, {
      method: 'DELETE',
    }),

  // accounts -- who can sign in
  listOrders: () => send('/api/orders'),
  setOrderStatus: (id, status) =>
    send(`/api/orders/${id}`, { method: 'PATCH', body: { status } }),
  createOrderFor: (customerId, payload) =>
    send(`/api/customers/${customerId}/orders`, { method: 'POST', body: payload }),
  reviseOrder: (orderId, payload) =>
    send(`/api/orders/${orderId}`, { method: 'PUT', body: payload }),
  listOrderChanges: (orderId) => send(`/api/orders/${orderId}/changes`),
  customerForUser: (userId) =>
    send(`/api/users/${userId}/customer`, { method: 'POST' }),
  listUsers: () => send('/api/users'),
  createUser: (payload) => send('/api/users', { method: 'POST', body: payload }),
  updateUser: (id, payload) =>
    send(`/api/users/${id}`, { method: 'PATCH', body: payload }),
  setUserPassword: (id, password) =>
    send(`/api/users/${id}/password`, { method: 'POST', body: { password } }),

  // customers -- who you ship to
  listCustomers: () => send('/api/customers'),
  updateCustomer: (id, payload) =>
    send(`/api/customers/${id}`, { method: 'PATCH', body: payload }),
  addCustomerAddress: (id, payload) =>
    send(`/api/customers/${id}/addresses`, { method: 'POST', body: payload }),
}
