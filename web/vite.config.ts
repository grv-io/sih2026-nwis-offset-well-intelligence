/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The SPA always talks to the API under /api/*. In dev Vite proxies that to
// uvicorn on :8000 (prefix stripped); in the single-process demo FastAPI strips
// the prefix itself (api/main.py, Phase 7 block), so one build works in both.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.NWIS_API_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
      },
    },
    build: {
      chunkSizeWarningLimit: 1600,
      rollupOptions: {
        output: {
          manualChunks: {
            plotly: ['plotly.js-basic-dist-min', 'react-plotly.js/factory'],
            leaflet: ['leaflet', 'react-leaflet'],
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      // theme.test.tsx reads tokens.css?raw to check contrast + the TS palette mirror
      css: { include: [/tokens\.css/] },
    },
  }
})
