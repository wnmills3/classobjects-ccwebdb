/**
 * The console's API client, at the seam the page tests cannot see.
 *
 * Every page test mocks `api` wholesale and asserts the arguments handed to
 * it. That is the right thing for a page, but it means the client's own
 * translation from those arguments into an HTTP body is exercised by
 * nothing -- and that translation is where a request can quietly come to
 * mean something else. These tests read the body actually sent.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { saveTokens } from '../shared/api'

function captureFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    status: 200,
    ok: true,
    text: () => Promise.resolve('{}'),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The parsed JSON body of the one request that was made. */
function sentBody(fetchMock) {
  return JSON.parse(fetchMock.mock.calls[0][1].body)
}

afterEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('updateImageLink', () => {
  // The endpoint distinguishes an omitted field from an explicit null, so
  // what is *absent* from the body is as meaningful as what is present.
  it('does not mention the role when only making a photograph primary', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { isPrimary: true })

    const body = sentBody(fetchMock)
    expect(body.is_primary).toBe(true)
    // The bug this covers: `image_role: null` here reads as "clear the
    // role", so promoting a photograph erased its obverse/reverse.
    expect('image_role' in body).toBe(false)
  })

  it('does not mention is_primary when only setting a role', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { imageRole: 'obverse' })

    const body = sentBody(fetchMock)
    expect(body.image_role).toBe('obverse')
    expect('is_primary' in body).toBe(false)
  })

  it('still sends an explicit null to clear a role', async () => {
    // The console's blank option. A truthiness check on `imageRole` would
    // drop this and make that option a no-op again -- which is the bug the
    // endpoint's `model_fields_set` was introduced to fix.
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { imageRole: null })

    const body = sentBody(fetchMock)
    expect('image_role' in body).toBe(true)
    expect(body.image_role).toBeNull()
  })

  it('always carries the for-sale acknowledgement', async () => {
    saveTokens({ access_token: 'a', refresh_token: 'r' })
    const fetchMock = captureFetch()

    await api.updateImageLink(9, { isPrimary: true, acknowledgeForSale: true })

    expect(sentBody(fetchMock).acknowledge_for_sale).toBe(true)
  })
})
