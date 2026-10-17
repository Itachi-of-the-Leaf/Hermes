import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development, /api calls go to the FastAPI server on :8000 (no CORS setup needed).
export default defineConfig({
  plugins: [react()],
  // two pages: the landing page at / (index.html) and the dashboard at /dashboard/ (dashboard/index.html)
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        dashboard: fileURLToPath(new URL('./dashboard/index.html', import.meta.url)),
      },
    },
  },
  test: { environment: 'jsdom', restoreMocks: true, setupFiles: ['./src/test/setup.ts'] },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
