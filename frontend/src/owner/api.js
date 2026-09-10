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

  // accounts -- who can sign in
  listUsers: () => send('/api/users'),
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
