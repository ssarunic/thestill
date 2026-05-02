import { defineConfig, devices } from '@playwright/test'

/**
 * Spec #28 §1327 — Playwright suite for the web UI.
 *
 * The dev server must already be running on http://localhost:5173
 * (e.g. `npm run dev`) before invoking `npm run test:e2e`. We don't
 * spawn it via `webServer` because the suite is meant to be a
 * developer aid, not a CI gate yet — Phase 5 will add the rest of
 * the surface and we'll wire CI then.
 *
 * Browsers must be installed once with `npx playwright install`.
 */
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
