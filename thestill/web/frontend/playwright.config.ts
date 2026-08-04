import { defineConfig, devices } from '@playwright/test'

/**
 * Spec #28 §1327 — Playwright suite for the web UI.
 * Spec #68 — promoted to a CI gate.
 *
 * **Server.** By default the suite boots its own `vite preview` (serving the
 * production build from `../static`) via `webServer`, so `npm run test:e2e`
 * works from a clean checkout with no manual setup — provided you've run
 * `npm run build` first. Point it at an already-running server instead with
 * `PLAYWRIGHT_BASE_URL=…`, which disables `webServer` entirely:
 *
 *   PLAYWRIGHT_BASE_URL=http://localhost:5173 npm run test:e2e   # vite dev
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 npm run test:e2e   # FastAPI
 *
 * **Tags.** Specs that need a real backend with real data are tagged `@live`
 * and are excluded from CI (`npm run test:e2e:ci`), which has no corpus.
 * Everything else stubs `/api/**` and is hermetic — those are the CI gate.
 *
 * Browsers install once with `npx playwright install chromium`.
 */
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:4173'

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Only manage a server when the caller hasn't supplied one. `vite preview`
  // serves the built SPA, so this exercises the same bundle CI ships rather
  // than a dev-server transform of it.
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'npm run preview',
        url: 'http://localhost:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
})
