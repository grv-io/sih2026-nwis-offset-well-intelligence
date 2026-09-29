import { defineConfig } from '@playwright/test'

// End-to-end pass over the static GitHub Pages build (VITE_STATIC_DEMO=1).
// Serve web/dist under /sih2026-nwis-offset-well-intelligence/ first, or point
// NWIS_STATIC_URL at the published site.
export default defineConfig({
  testDir: './e2e-static',
  timeout: 240_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: process.env.NWIS_STATIC_URL || 'http://127.0.0.1:4390/sih2026-nwis-offset-well-intelligence/',
    channel: 'chromium',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
  },
})
