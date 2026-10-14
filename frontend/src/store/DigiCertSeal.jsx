import { useEffect } from 'react'

/** The container DigiCert's script fills; the id and tag are the site's own. */
const SEAL_ID = 'DigiCertClickID_kOj5Uc3j'
const SEAL_TAG = 'kOj5Uc3j'
const SEAL_SCRIPT = 'https://seal.digicert.com/seals/cascade/seal.min.js'

/**
 * DigiCert's site seal for the certificate the shop is served under.
 *
 * DigiCert's script looks up the container by id when it loads, so it is
 * added only once this component has rendered that container, and only once
 * per page: a second copy (React's StrictMode mounts twice) would draw a second
 * seal. The seal shows only on the domain the certificate names; elsewhere,
 * on a development server for one, the container stays empty.
 */
export default function DigiCertSeal() {
  useEffect(() => {
    if (document.querySelector(`script[src="${SEAL_SCRIPT}"]`)) return
    window.__dcid = window.__dcid || []
    window.__dcid.push({ cid: SEAL_ID, tag: SEAL_TAG })
    const script = document.createElement('script')
    script.async = true
    script.src = SEAL_SCRIPT
    document.body.appendChild(script)
  }, [])

  return <div id={SEAL_ID} className="seal" />
}
