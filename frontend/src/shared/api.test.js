import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, clearTokens, loadTokens, saveTokens, send } from './api'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('token storage', () => {
  it('round-trips a token pair', () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    expect(loadTokens()).toEqual({ access_token: 'a', refresh_token: 'r' })
  })

  it('returns null when nothing is stored', () => {
    expect(loadTokens()).toBeNull()
  })

  it('clears the stored pair', () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    clearTokens()
    expect(loadTokens()).toBeNull()
  })

  it('returns null rather than throwing when the stored value is corrupt', () => {
    // Hand-edited or half-written storage must not take the whole app down on
    // the first render.
    localStorage.setItem('ccwebdb.tokens', '{not json')
    expect(loadTokens()).toBeNull()
  })

  it('stays usable when storage itself throws', () => {
    // Private browsing and blocked-site-data both make setItem throw. The app
    // should carry on signed in for the tab rather than crash.
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    expect(() => saveTokens({ access_token: 'a' })).not.toThrow()
  })
})

describe('send', () => {
  function mockFetch(body = {}) {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      text: () => Promise.resolve(JSON.stringify(body)),
    })
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('hands a FormData body to fetch untouched, with no Content-Type header', async () => {
    // The multipart trap: the browser must set Content-Type itself, boundary
    // included. A handler that JSON-stringified this or set the header by
    // hand would produce a request the server cannot parse -- so this checks
    // both halves, not just that the call happened.
    const fetchMock = mockFetch()
    const form = new FormData()
    form.append('file', new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }))

    await send('/api/images', { method: 'POST', body: form, auth: false })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBe(form)
    expect(init.headers['Content-Type']).toBeUndefined()
  })

  it('still JSON-encodes a plain object body and sets Content-Type', async () => {
    // Guards the FormData branch from swallowing the existing JSON path.
    const fetchMock = mockFetch()
    await send('/api/orders', { method: 'POST', body: { items: [1] }, auth: false })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBe(JSON.stringify({ items: [1] }))
    expect(init.headers['Content-Type']).toBe('application/json')
  })
})

describe('ApiError', () => {
  it('uses a string detail as the message', () => {
    const err = new ApiError(404, 'Order not found')
    expect(err.message).toBe('Order not found')
    expect(err.status).toBe(404)
    expect(err.detail).toBe('Order not found')
  })

  it('falls back to a generic message when the detail is structured', () => {
    // FastAPI's validation errors arrive as a list, which would render as
    // "[object Object]" if used directly as the message.
    const detail = [{ loc: ['body', 'email'], msg: 'field required' }]
    const err = new ApiError(422, detail)
    expect(err.message).toBe('Request failed')
    expect(err.detail).toEqual(detail)
  })

  it('is an Error, so existing catch blocks keep working', () => {
    expect(new ApiError(500, 'boom')).toBeInstanceOf(Error)
  })
})
