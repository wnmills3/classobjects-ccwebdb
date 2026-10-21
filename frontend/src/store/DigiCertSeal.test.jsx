import { render } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DigiCertSeal from './DigiCertSeal'

const SEAL_SCRIPT = 'https://seal.digicert.com/seals/cascade/seal.min.js'
const CID = 'DigiCertClickID_kOj5Uc3j'

const scripts = () => document.querySelectorAll(`script[src="${SEAL_SCRIPT}"]`)

beforeEach(() => {
  scripts().forEach((s) => s.remove())
  delete window.__dcid
  vi.stubEnv('VITE_DIGICERT_SEAL_ID', 'kOj5Uc3j')
})

afterEach(() => vi.unstubAllEnvs())

describe('DigiCertSeal', () => {
  it('loads the script afresh when the seal is mounted again', () => {
    // The script draws into the container it finds when it runs. A second
    // container, rendered after it ran, stays empty unless it runs again.
    const first = render(<DigiCertSeal />)
    const loaded = scripts()[0]
    expect(loaded).toBeDefined()
    first.unmount()

    render(<DigiCertSeal />)

    expect(scripts()).toHaveLength(1)
    expect(scripts()[0]).not.toBe(loaded)
    expect(document.getElementById(CID)).not.toBeNull()
  })

  it('asks for one seal per container however often it mounts', () => {
    render(
      <StrictMode>
        <DigiCertSeal />
      </StrictMode>,
    ).unmount()
    render(<DigiCertSeal />)

    expect(scripts()).toHaveLength(1)
    expect(window.__dcid).toEqual([{ cid: CID, tag: 'kOj5Uc3j' }])
  })
})
