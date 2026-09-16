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

  // catalogue writes -- reads are in shared/api.js, the shop needs those
  createCatalogItem: (payload) =>
    send('/api/catalog', { method: 'POST', body: payload }),
  updateCatalogItem: (id, payload) =>
    send(`/api/catalog/${id}`, { method: 'PATCH', body: payload }),
  deleteCatalogItem: (id) => send(`/api/catalog/${id}`, { method: 'DELETE' }),

  renameReferenceValue: (table, code, payload) =>
    send(`/api/reference/${table}/${code}`, { method: 'PATCH', body: payload }),
  // Retired values and removed aliases included, for the Vocabularies page.
  getReferenceForEditing: (table) =>
    send(`/api/reference/${table}?${query({ include_inactive: true })}`),
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
  createPurchaseOrder: (payload) =>
    send('/api/purchase-orders', { method: 'POST', body: payload }),
  createInventoryItem: (payload) =>
    send('/api/inventory', { method: 'POST', body: payload }),
  // What the facts entered so far decide: a note's class, seal, signatures
  // and Reserve Bank, or a coin's metal. See app/routers/defaults.py.
  suggestNote: (params = {}) => send(`/api/defaults/note?${query(params)}`),
  suggestCoin: (params = {}) => send(`/api/defaults/coin?${query(params)}`),

  // images -- evidence a person looked at the object, attached to an item
  uploadImage: (inventoryItemId, file, { imageRole, isPrimary = false } = {}) => {
    const form = new FormData()
    form.append('file', file)
    form.append('inventory_item_id', String(inventoryItemId))
    if (imageRole) form.append('image_role', imageRole)
    form.append('is_primary', String(isPrimary))
    return send('/api/images', { method: 'POST', body: form })
  },

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
