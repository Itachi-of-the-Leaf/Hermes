import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development, /api calls go to the FastAPI server on :8000 (no CORS setup needed).
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', restoreMocks: true },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
