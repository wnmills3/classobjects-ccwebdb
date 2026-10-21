import { useEffect } from 'react'

const SEAL_SCRIPT = 'https://seal.digicert.com/seals/cascade/seal.min.js'

/**
 * DigiCert's site seal for the certificate the shop is served under.
 *
 * The seal belongs to one certificate, so its tag is a setting,
 * VITE_DIGICERT_SEAL_ID in the repository's .env, read when the frontend is
 * built; with none set the shop shows no seal and loads no script.
 *
 * DigiCert's script draws into the container it finds by id when it runs,
 * so it is added only once this component has rendered that container. A
 * container rendered later -- the footer remounts when the shell changes,
 * the closed shop's for the open one's -- starts empty, so each mount
 * replaces the script with a fresh copy that draws into it. There is never
 * more than one copy, nor more than one request for this container in
 * `__dcid`: either would draw a second seal (React's StrictMode mounts
 * twice). The seal shows only on the domain the certificate names;
 * elsewhere, on a development server for one, the container stays empty.
 */
export default function DigiCertSeal() {
  const tag = import.meta.env.VITE_DIGICERT_SEAL_ID ?? ''
  const id = `DigiCertClickID_${tag}`

  useEffect(() => {
    if (!tag) return
    document.querySelectorAll(`script[src="${SEAL_SCRIPT}"]`).forEach((s) => s.remove())
    window.__dcid = (window.__dcid || []).filter((entry) => entry?.cid !== id)
    window.__dcid.push({ cid: id, tag })
    const script = document.createElement('script')
    script.async = true
    script.src = SEAL_SCRIPT
    document.body.appendChild(script)
  }, [id, tag])

  if (!tag) return null
  return <div id={id} className="seal" />
}
