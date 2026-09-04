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
})
