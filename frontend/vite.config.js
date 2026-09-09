import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two applications in one project: the shop and the owner console.
 *
 * Rollup builds each HTML entry as an independent module graph, which is what
 * keeps owner code out of the shop's bundle. `scripts/check-bundle-isolation.mjs`
 * asserts that against the built manifest rather than trusting it.
 */
function twoAppDevFallback() {
  return {
    name: 'ccwebdb-two-app-dev-fallback',
    // Returning a function post-hooks this middleware, so it runs after Vite's
    // own static and transform handling and only sees what would 404.
    configureServer(server) {
      return () => {
        server.middlewares.use((req, _res, next) => {
          const [path] = (req.url ?? '/').split('?')
          // Anything with an extension, or Vite's own internals, is a real
          // asset request and must not be rewritten to an HTML document.
          if (
            /\.[^/]+$/.test(path) ||
            path.startsWith('/@') ||
            path.startsWith('/src/') ||
            path.startsWith('/node_modules/')
          ) {
            return next()
          }
          req.url =
            path === '/owner' || path.startsWith('/owner/')
              ? '/owner.html'
              : '/index.html'
          next()
        })
      }
    },
  }
}

export default defineConfig({
  // 'mpa' switches off the single-entry SPA fallback, which would send
  // /owner/inventory/coins to the shop. twoAppDevFallback replaces it with one
  // that knows about both entries.
  appType: 'mpa',
  plugins: [react(), twoAppDevFallback()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Read by scripts/check-bundle-isolation.mjs.
    manifest: true,
    rollupOptions: {
      input: {
        store: 'index.html',
        owner: 'owner.html',
      },
    },
  },
  // Vitest reads this file, so the test run gets the same plugin and resolution
  // rules as the app rather than a second, drifting configuration.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    coverage: {
      provider: 'v8',
      // lcov is what SonarQube reads; text keeps the number visible in the
      // terminal so a drop is noticed before the scan runs.
      reporter: ['text-summary', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{js,jsx}'],
      // The entry modules only mount their app, and the test helpers are not
      // the subject.
      exclude: ['src/store/main.jsx', 'src/owner/main.jsx', 'src/test/**'],
    },
  },
})
