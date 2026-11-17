/**
 * Spec #117 — Follow beside the show name on the episode page: the click,
 * the explaining toast and Undo, on a phone. Hermetic (every /api/** call
 * stubbed, follow and unfollow recorded).
 */
import { test, expect } from '@playwright/test'
import { EPISODE_PATH, PHONE, mockEpisodeApi } from './episode-hero-fixture'

// The narrowest supported width: the long confirmation must still fit.
test.use({ viewport: { width: 320, height: PHONE.height }, isMobile: true, hasTouch: true })

test('Follow explains itself, and Undo unfollows', async ({ page }) => {
  await mockEpisodeApi(page)
  const calls: string[] = []
  await page.route('**/api/podcasts/prof-g-markets/follow*', (route) => {
    const request = route.request()
    calls.push(`${request.method()} ${new URL(request.url()).search}`)
    if (request.method() === 'POST') {
      return route.fulfill({
        status: 201,
        json: { status: 'ok', message: 'Successfully followed podcast', podcast_slug: 'prof-g-markets', followed_at: '2026-11-17T12:00:00Z' },
      })
    }
    return route.fulfill({ status: 204, body: '' })
  })

  await page.goto(EPISODE_PATH)
  await page.getByRole('button', { name: 'Follow', exact: true }).click()

  await expect(page.getByText(/Following Prof G Markets\. New episodes will arrive summarised in your inbox and briefing\./)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Following', exact: true })).toBeDisabled()
  expect(calls).toEqual(['POST ?source=episode_page'])
  await page.waitForTimeout(800)
  const toast = (await page.getByRole('button', { name: 'Undo' }).locator('xpath=..').boundingBox())!
  expect(toast.x).toBeGreaterThanOrEqual(0)
  expect(toast.x + toast.width).toBeLessThanOrEqual(320)
  await page.screenshot({ path: 'test-results/spec117-followed-phone.png' })

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Unfollowed Prof G Markets')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Follow', exact: true })).toBeEnabled()
  expect(calls).toEqual(['POST ?source=episode_page', 'DELETE '])
})
