/**
 * What a build of the shop says when it will carry no DigiCert seal.
 *
 * The seal's tag is read from .env when the frontend is built, and with none
 * the shop builds and serves perfectly well -- with no seal, and nothing
 * said. So a production build says so; the development server and the tests
 * never show the seal anyway.
 *
 * Its own module, not vite.config.js, so a test can import it: the config
 * reads dev-server.json through import.meta.url, which is no file under the
 * test runner.
 */
export function sealWarning(command, mode, env) {
  if (command !== 'build' || mode !== 'production') return null
  if (env.VITE_DIGICERT_SEAL_ID) return null
  return 'VITE_DIGICERT_SEAL_ID is not set in .env: this build of the shop has no DigiCert seal'
}
