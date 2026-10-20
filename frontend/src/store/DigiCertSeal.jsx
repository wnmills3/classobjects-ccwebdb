import { useEffect } from 'react'

const SEAL_SCRIPT = 'https://seal.digicert.com/seals/cascade/seal.min.js'

/**
 * DigiCert's site seal for the certificate the shop is served under.
 *
 * The seal belongs to one certificate, so its tag is a setting,
 * VITE_DIGICERT_SEAL_ID in the repository's .env, read when the frontend is
 * built; with none set the shop shows no seal and loads no script.
 *
 * DigiCert's script looks up the container by id when it loads, so it is
 * added only once this component has rendered that container, and only once
 * per page: a second copy (React's StrictMode mounts twice) would draw a second
 * seal. The seal shows only on the domain the certificate names; elsewhere,
 * on a development server for one, the container stays empty.
 */
export default function DigiCertSeal() {
  const tag = import.meta.env.VITE_DIGICERT_SEAL_ID ?? ''
  const id = `DigiCertClickID_${tag}`

  useEffect(() => {
    if (!tag) return
    if (document.querySelector(`script[src="${SEAL_SCRIPT}"]`)) return
    window.__dcid = window.__dcid || []
    window.__dcid.push({ cid: id, tag })
    const script = document.createElement('script')
    script.async = true
    script.src = SEAL_SCRIPT
    document.body.appendChild(script)
  }, [id, tag])

  if (!tag) return null
  return <div id={id} className="seal" />
}
