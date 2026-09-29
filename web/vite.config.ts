/// <reference types="vitest" />
import { copyFileSync, existsSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// GitHub Pages has no SPA routing. It does serve /<repo>/office from office.html (with a
// 200), so the app's two routes get a copy of index.html each and deep links such as
// /<repo>/office?well=DUL-005&r=5 load cleanly; any other path gets 404.html, also a
// copy, so it still boots the app (the router then redirects to /office).
// The recorded demo data lives in public/demo-data/ (scripts/build_static_demo.py) and
// only the static build needs it; keep it out of the normal build served by FastAPI.
function dropDemoData(): Plugin {
  let outDir = 'dist'
  return {
    name: 'nwis-drop-demo-data',
    apply: 'build',
    configResolved(c) {
      outDir = resolve(c.root, c.build.outDir)
    },
    closeBundle() {
      rmSync(resolve(outDir, 'demo-data'), { recursive: true, force: true })
    },
  }
}

function spaFallback(): Plugin {
  let outDir = 'dist'
  return {
    name: 'nwis-spa-404-fallback',
    apply: 'build',
    configResolved(c) {
      outDir = resolve(c.root, c.build.outDir)
    },
    closeBundle() {
      const index = resolve(outDir, 'index.html')
      if (!existsSync(index)) return
      for (const page of ['404.html', 'office.html', 'rig.html']) copyFileSync(index, resolve(outDir, page))
    },
  }
}

// The SPA always talks to the API under /api/*. In dev Vite proxies that to
// uvicorn on :8000 (prefix stripped); in the single-process demo FastAPI strips
// the prefix itself (api/main.py, Phase 7 block), so one build works in both.
// The static demo build (VITE_STATIC_DEMO=1) never calls /api at all.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.NWIS_API_TARGET || 'http://127.0.0.1:8000'
  // VITE_STATIC_DEMO=1: the static GitHub Pages build (recorded responses, no server),
  // served from https://<user>.github.io/<repo>/, hence the base path.
  const staticDemo = env.VITE_STATIC_DEMO === '1'
  return {
    base: staticDemo ? env.VITE_BASE_PATH || '/sih2026-nwis-offset-well-intelligence/' : '/',
    plugins: staticDemo ? [react(), spaFallback()] : [react(), dropDemoData()],
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
