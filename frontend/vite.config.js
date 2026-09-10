import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two applications in one project: the shop and the owner console.
 *
 * Rollup builds each HTML entry as an independent module graph, which is what
 * keeps owner code out of the shop's bundle. `scripts/check-bundle-isolation.mjs`
 * asserts that against the emitted bundle graph (see `bundleGraph()` below)
 * rather than trusting it -- Vite's own manifest cannot show chunk
 * membership, only chunk imports.
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

/**
 * Emit which modules ended up in which chunk.
 *
 * Vite's own manifest cannot answer this: it records a chunk's imports but not
 * its contents, and a chunk shared between entries has no `src` attributing it
 * to a source tree. Rollup only tells you inside `generateBundle`, so that is
 * where this listens.
 */
function bundleGraph() {
  return {
    name: 'ccwebdb-bundle-graph',
    generateBundle(_options, bundle) {
      const root = process.cwd().replace(/\\/g, '/')
      const chunks = {}
      for (const [fileName, chunk] of Object.entries(bundle)) {
        if (chunk.type !== 'chunk') continue
        chunks[fileName] = {
          name: chunk.name,
          isEntry: chunk.isEntry,
          imports: chunk.imports,
          dynamicImports: chunk.dynamicImports,
          modules: Object.keys(chunk.modules).map((id) =>
            id.replace(/\\/g, '/').replace(root + '/', ''),
          ),
        }
      }
      this.emitFile({
        type: 'asset',
        fileName: '.vite/bundle-graph.json',
        source: JSON.stringify(chunks, null, 2),
      })
    },
  }
}

export default defineConfig({
  // 'mpa' switches off the single-entry SPA fallback, which would send
  // /owner/inventory/coins to the shop. twoAppDevFallback replaces it with one
  // that knows about both entries.
  appType: 'mpa',
  plugins: [react(), twoAppDevFallback(), bundleGraph()],
  server: {
    // Bind IPv4 loopback explicitly. Left unset, Vite binds only [::1] on this
    // machine, and every documented URL in docs/runtime-operations.md and in
    // this plan says 127.0.0.1 -- so the documented commands fail with
    // "connection refused" against a server that is running perfectly well.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Not read by the isolation check -- that reads the richer bundle graph
    // emitted above, because a manifest records a chunk's imports but never
    // its contents. Kept because a manifest is what a server needs to map an
    // entry to its hashed asset when these are eventually served for real.
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
    // UTC, to match the database: every stored timestamp is
    // `DateTime(timezone=True)` written by `datetime.now(UTC)`, and local time
    // belongs only at the display/input edge. Running the suite in UTC keeps
    // it reproducible regardless of which machine or CI runner executes it.
    //
    // The one deliberate exception is ReceiptPanel.test.jsx's arrival-date
    // test: under a UTC runner, "local date" and "UTC date" are the same
    // value, so that test cannot tell a correct local-date default apart from
    // a regressed UTC-based one. That test overrides `process.env.TZ` for its
    // own duration (Node re-reads TZ live, including for already-constructed
    // `Date` objects -- verified empirically, not assumed) and restores it
    // afterwards, rather than the whole suite paying for one test's need.
    env: {
      TZ: 'UTC',
    },
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
