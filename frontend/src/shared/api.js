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
  /**
   * `body` is the whole parsed response, kept beside the flattened `detail`,
   * or null when the response had no JSON body.
   *
   * Some refusals carry more than a sentence: `POST /api/offers` answers a
   * 409 with `{detail, refused: [{item_code, reason}]}`, and the console has
   * to name every refused item with its reason. `readDetail` reduces the body
   * to one string, so the per-item reasons are only on `body`.
   */
  constructor(status, detail, body = null) {
    super(typeof detail === 'string' ? detail : 'Request failed')
    this.status = status
    this.detail = detail
    this.body = body
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

//: The refresh in flight, shared by every request refused while it runs.
let refreshing = null

/**
 * One refresh for however many requests were refused together.
 *
 * A page that loads several things at once on an expired access token gets a
 * 401 for each. Refreshing once per 401 would spend the refresh token that
 * many times over; they all wait on the same one instead.
 */
function refreshOnce() {
  refreshing ??= refreshTokens().finally(() => {
    refreshing = null
  })
  return refreshing
}

/** Parse a response body as JSON; `undefined` when it is not JSON at all. */
function parseBody(text) {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

/**
 * `path` with `params` as its query string, leaving out the ones not given.
 *
 * Empty strings, null and undefined are left out: a blank filter box asks for
 * nothing. `false` is kept -- `mine=false` can mean something -- unless
 * `dropFalse` says an unticked box asks for nothing too. An array repeats its
 * key once per value (`item_ids=3&item_ids=5`).
 */
export function withQuery(path, params = {}, { dropFalse = false } = {}) {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === null || value === undefined) continue
    if (dropFalse && value === false) continue
    for (const one of Array.isArray(value) ? value : [value]) qs.append(key, one)
  }
  const query = qs.toString()
  return query ? `${path}?${query}` : path
}

/**
 * The one place a request is made, exported so `management/api.js` can build the
 * console's calls on the same token handling and error shape.
 */
export async function send(path, { method = 'GET', body, form, auth = true } = {}) {
  const headers = {}
  let payload

  if (form) {
    payload = new URLSearchParams(body)
  } else if (body instanceof FormData) {
    // Multipart body (an image upload). Handed to `fetch` untouched: the
    // browser sets `Content-Type` itself, boundary included, and setting it
    // by hand here would produce a request the server cannot parse.
    payload = body
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

  const sentWith = loadTokens()
  let res = await fetch(path, {
    method,
    headers: withAuth(sentWith),
    body: payload,
  })

  if (res.status === 401 && auth) {
    // Another request may already have refreshed while this one was out;
    // its tokens are then the ones to retry with.
    const stored = loadTokens()
    const fresh =
      stored?.access_token && stored.access_token !== sentWith?.access_token
        ? stored
        : await refreshOnce()
    if (fresh) {
      res = await fetch(path, {
        method,
        headers: withAuth(fresh),
        body: payload,
      })
    }
  }

  if (res.status === 204) return null

  // A proxy or a crashed server can answer with an HTML page. That is still
  // a failed request with a status, not a SyntaxError from the parser.
  const parsed = parseBody(await res.text())

  if (!res.ok) {
    if (res.status === 401) clearTokens()
    if (parsed === undefined) {
      throw new ApiError(res.status, res.statusText || `Request failed (${res.status})`)
    }
    throw new ApiError(res.status, readDetail(parsed), parsed)
  }
  if (parsed === undefined) {
    throw new ApiError(res.status, 'The server sent a response that is not JSON')
  }
  return parsed
}

/**
 * The calls the shop makes, plus the ones shared components make.
 *
 * The console's calls are deliberately NOT here -- see `management/api.js`. This
 * object is a single literal, so nothing tree-shakes out of it: every path
 * written here is downloaded by every anonymous visitor to the shop. A list
 * of `/api/inventory/...` and `/api/users/...` endpoints is a map of the
 * owner's tooling -- the same reason the console's styles are a stylesheet
 * of their own.
 */
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

  // catalog
  //
  // A catalog entry is a listing plus the inventory item behind it. The
  // id in these paths is the LISTING id -- the item id is carried alongside
  // as inventory_item_id, for the admin views that need it.
  listCatalog: (params = {}) =>
    send(withQuery('/api/catalog', params, { dropFalse: true }), { auth: false }),
  getCatalogItem: (id) => send(`/api/catalog/${id}`, { auth: false }),

  // reference vocabularies, for dropdowns
  listReferenceTables: () => send('/api/reference', { auth: false }),
  // Retired values included: a record may still use one, and its picker has
  // to show it. `useReference` leaves them out for everything else.
  getReference: (table) =>
    send(`/api/reference/${table}?include_inactive=true`, { auth: false }),
  // Shared because `shared/reference.jsx`'s ReferenceSelect calls it. Only
  // console pages render that component today, but the component lives here,
  // and shared code may not import from management/.
  addReferenceValue: (table, payload) =>
    send(`/api/reference/${table}`, { method: 'POST', body: payload }),

  // orders
  createOrder: (items) => send('/api/orders', { method: 'POST', body: { items } }),
  // Mine even for an administrator: in the shop they are a customer. Every
  // order, and changing status, are console tools in management/api.js.
  listMyOrders: () => send('/api/orders?mine=true'),
}
