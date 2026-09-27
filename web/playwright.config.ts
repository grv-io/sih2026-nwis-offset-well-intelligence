import { defineConfig } from '@playwright/test'

// Smoke test against the single-process demo server (run_demo.ps1 / uvicorn on :8000
// serving web/dist). Start it first; set NWIS_BASE_URL to point elsewhere (e.g. :5173).
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: process.env.NWIS_BASE_URL || 'http://127.0.0.1:8000',
    channel: 'chromium', // full Chromium in new-headless mode
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
  },
})
