import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, clearTokens, loadTokens, saveTokens } from './api'

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
