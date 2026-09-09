import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to the FastAPI backend, so the browser only ever
// talks to one origin during development and CORS never comes into play.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
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
      // main.jsx only mounts the app, and the test helpers are not the subject.
      exclude: ['src/main.jsx', 'src/test/**'],
    },
  },
})
