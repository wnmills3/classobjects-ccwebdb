// Thin fetch wrapper around the FastAPI backend.
//
// Access tokens are short lived; on a 401 we transparently refresh once using
// the stored refresh token and replay the original request. If that fails the
// caller is signed out.

const TOKEN_KEY = 'ccwebdb.tokens'

export function loadTokens() {
  try {
    const raw = localStorage.getItem(TOKEN_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveTokens(tokens) {
  try {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens))
  } catch {
    /* private browsing or blocked storage - stay signed in for this tab only */
  }
}

export function clearTokens() {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : 'Request failed')
    this.status = status
    this.detail = detail
  }
}

// FastAPI returns validation errors as a list of objects; flatten to a string.
function readDetail(body) {
  const detail = body?.detail
  if (!detail) return 'Request failed'
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
  }
  return 'Request failed'
}

async function refreshTokens() {
  const tokens = loadTokens()
  if (!tokens?.refresh_token) return null

  const res = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: tokens.refresh_token }),
  })
  if (!res.ok) return null

  const fresh = await res.json()
  saveTokens(fresh)
  return fresh
}

async function send(path, { method = 'GET', body, form, auth = true } = {}) {
  const headers = {}
  let payload

  if (form) {
    payload = new URLSearchParams(body)
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const withAuth = (tokens) => {
    const h = { ...headers }
    if (auth && tokens?.access_token) {
      h.Authorization = `Bearer ${tokens.access_token}`
    }
    return h
  }

  let res = await fetch(path, {
    method,
    headers: withAuth(loadTokens()),
    body: payload,
  })

  if (res.status === 401 && auth) {
    const fresh = await refreshTokens()
    if (fresh) {
      res = await fetch(path, {
        method,
        headers: withAuth(fresh),
        body: payload,
      })
    }
  }

  if (res.status === 204) return null

  const text = await res.text()
  const parsed = text ? JSON.parse(text) : null

  if (!res.ok) {
    if (res.status === 401) clearTokens()
    throw new ApiError(res.status, readDetail(parsed))
  }
  return parsed
}

export const api = {
  // auth
  login: (email, password) =>
    send('/api/auth/login', {
      method: 'POST',
      form: true,
      auth: false,
      body: { username: email, password },
    }),
  register: (payload) =>
    send('/api/auth/register', { method: 'POST', body: payload, auth: false }),
  me: () => send('/api/auth/me'),

  // catalogue
  //
  // A catalogue entry is a listing plus the inventory item behind it. The
  // id in these paths is the LISTING id -- the item id is carried alongside
  // as inventory_item_id, for the admin views that need it.
  listCatalog: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined && v !== false) qs.set(k, v)
    })
    const query = qs.toString()
    const suffix = query ? `?${query}` : ''
    return send(`/api/catalog${suffix}`, { auth: false })
  },
  getCatalogItem: (id) => send(`/api/catalog/${id}`, { auth: false }),
  createCatalogItem: (payload) =>
    send('/api/catalog', { method: 'POST', body: payload }),
  updateCatalogItem: (id, payload) =>
    send(`/api/catalog/${id}`, { method: 'PATCH', body: payload }),
  deleteCatalogItem: (id) => send(`/api/catalog/${id}`, { method: 'DELETE' }),

  // reference vocabularies, for dropdowns
  listReferenceTables: () => send('/api/reference', { auth: false }),
  getReference: (table) => send(`/api/reference/${table}`, { auth: false }),

  addReferenceValue: (table, payload) =>
    send(`/api/reference/${table}`, { method: 'POST', body: payload }),
  renameReferenceValue: (table, code, payload) =>
    send(`/api/reference/${table}/${code}`, { method: 'PATCH', body: payload }),

  // inventory (staff)
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

  // orders
  createOrder: (items) => send('/api/orders', { method: 'POST', body: { items } }),
  listOrders: () => send('/api/orders'),
  setOrderStatus: (id, status) =>
    send(`/api/orders/${id}`, { method: 'PATCH', body: { status } }),

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
