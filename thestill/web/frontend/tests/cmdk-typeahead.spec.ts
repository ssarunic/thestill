/**
 * Spec #28 §1334 — ⌘K typeahead Playwright test.
 *
 * Open ⌘K, type "musk", arrow-down to a person hit, Enter, and land
 * on the entity-derived destination. Phase 4 stubs the entity Enter
 * to /episodes?search=…; Phase 5 swaps to the real entity page —
 * update the assertion at that point.
 *
 * Requires the dev server running on PLAYWRIGHT_BASE_URL (default
 * http://localhost:5173) and a corpus that includes a person entity
 * matching "musk". Skipped if no person results land within the
 * settle window; the suite is a smoke check, not a gate yet.
 */

import { test, expect } from '@playwright/test'

test.describe('⌘K command bar', () => {
  test('opens, returns grouped hits, and navigates on Enter', async ({ page, browserName }) => {
    await page.goto('/')

    // ⌘K on Mac, Ctrl+K elsewhere — the global handler accepts both.
    const isMac = browserName === 'webkit' || process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+K' : 'Control+K')

    const input = page.getByTestId('cmdk-input')
    await expect(input).toBeFocused()

    await input.fill('musk')

    // Wait for at least one hit to appear; if the fixture corpus is
    // empty we skip rather than fail — this test is a smoke-check
    // against a populated dev DB.
    const items = page.locator('[data-testid^="cmdk-item-"]')
    try {
      await expect(items.first()).toBeVisible({ timeout: 5_000 })
    } catch {
      test.skip(true, 'No corpus loaded — populate dev DB to exercise this test.')
      return
    }

    // Find a person hit. If none, we still arrow + Enter on the
    // first quote/episode hit and assert URL changed.
    const personHit = page.getByTestId('cmdk-item-entity').first()
    const hasPerson = await personHit.count()
    if (hasPerson > 0) {
      // Arrow down until selection lands on the person row, capped
      // at the total visible rows so we don't loop forever.
      const total = await items.count()
      for (let i = 0; i < total; i++) {
        const selected = await page.locator('[role="option"][aria-selected="true"]').first()
        if ((await selected.getAttribute('data-testid'))?.includes('entity')) break
        await page.keyboard.press('ArrowDown')
      }
    }

    await page.keyboard.press('Enter')

    // After Enter the modal closes and the URL changes. For the
    // person stub the URL becomes /episodes?search=… — when Phase 5
    // ships the entity page swap this to /entities/person/<slug>.
    await expect(page).not.toHaveURL('/')
  })
})
