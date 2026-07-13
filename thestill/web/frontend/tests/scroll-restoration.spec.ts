/**
 * Verifies the app-wide list-page convention: navigating into a detail page
 * and pressing browser Back restores BOTH the filters and the scroll position.
 *
 * Runs against the Vite dev server with every /api/** call intercepted, so it
 * exercises the real routing + useScrollRestoration hook in a real Chromium
 * without needing the backend. Requires the dev server on PLAYWRIGHT_BASE_URL
 * (default http://localhost:5173).
 */
import { test, expect, type Page } from '@playwright/test'

const REGIONS = ['gb', 'us', 'de']

function topPodcastsBody(region: string, q?: string, category?: string) {
  // 60 rows → a comfortably tall, scrollable list. Each row carries a
  // podcast_slug so a click navigates straight to the detail route (no
  // resolve round-trip).
  const rows = Array.from({ length: 60 }, (_, i) => ({
    rank: i + 1,
    name: `${region.toUpperCase()} Show ${i + 1}`,
    artist: `Artist ${i + 1}`,
    rss_url: `https://example.com/${region}/${i + 1}`,
    apple_url: null,
    youtube_url: null,
    category: category ?? 'Comedy',
    source_genre: null,
    is_following: false,
    podcast_slug: `show-${region}-${i + 1}`,
    image_url: null,
  }))
  return {
    status: 'ok',
    timestamp: '2026-07-13T00:00:00Z',
    region,
    available_regions: REGIONS,
    available_categories: ['Comedy', 'History', 'Technology'],
    user_region: 'gb',
    count: rows.length,
    top_podcasts: q === '__none__' ? [] : rows,
  }
}

async function mockApi(page: Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        multi_user: false,
        authenticated: true,
        email_delivery_available: false,
        user: {
          id: 'u1',
          email: 'test@example.com',
          name: 'Test',
          picture: null,
          created_at: '2026-01-01T00:00:00Z',
          last_login_at: null,
          region: 'gb',
          region_locked: false,
          is_admin: true,
        },
      },
    }),
  )

  await page.route('**/api/top-podcasts*', (route) => {
    const url = new URL(route.request().url())
    const region = url.searchParams.get('region') ?? 'gb'
    const q = url.searchParams.get('q') ?? undefined
    const category = url.searchParams.get('category') ?? undefined
    route.fulfill({ json: topPodcastsBody(region, q, category) })
  })

  // Detail page — minimal payload so PodcastDetail mounts (it scroll-resets
  // itself, mimicking a real detail view). Shape doesn't need to be perfect;
  // we only need the /top route to unmount and remount on Back.
  await page.route('**/api/podcasts/**', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-07-13T00:00:00Z',
        podcast: {
          index: 1,
          id: 'p1',
          slug: 'show-gb-1',
          title: 'Detail Show',
          author: 'Author',
          description: 'A show',
          image_url: null,
          rss_url: 'https://example.com/gb/1',
          website_url: null,
          episode_count: 0,
          is_following: true,
        },
        episodes: [],
        total: 0,
        has_more: false,
      },
    }),
  )
}

test('Top Podcasts restores scroll position and filters on Back', async ({ page }) => {
  await mockApi(page)

  // Disable the browser's own (unreliable) scroll restoration so this test
  // proves useScrollRestoration itself puts the user back — not the browser.
  await page.addInitScript(() => {
    window.history.scrollRestoration = 'manual'
  })

  await page.goto('/top')

  // Wait for the list to render.
  await expect(page.getByText('GB Show 1', { exact: true })).toBeVisible()

  // Apply a filter so we can prove it survives Back too.
  await page.getByLabel('Filter by category').selectOption('History')
  await expect(page).toHaveURL(/category=History/)
  await expect(page.getByText('GB Show 1', { exact: true })).toBeVisible()

  // Scroll well down the list.
  await page.evaluate(() => window.scrollTo(0, 1200))
  await page.waitForFunction(() => window.scrollY > 1000)
  const before = await page.evaluate(() => window.scrollY)
  expect(before).toBeGreaterThan(1000)

  // Open a podcast detail (row ~15, below the fold).
  await page.getByRole('link', { name: 'Open GB Show 15', exact: true }).click()
  await expect(page).toHaveURL(/\/podcasts\/show-gb-15/)

  // A real detail page sits at the top. Force the window there so the Back
  // restoration has real work to do (and native restoration can't cheat).
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForFunction(() => window.scrollY === 0)

  // Back to the list.
  await page.goBack()
  await expect(page).toHaveURL(/\/top/)

  // Filter restored…
  await expect(page).toHaveURL(/category=History/)
  await expect(page.getByLabel('Filter by category')).toHaveValue('History')

  // …and scroll position restored (within a small tolerance).
  await page.waitForFunction(
    (y) => Math.abs(window.scrollY - y) < 30,
    before,
    { timeout: 5000 },
  )
  const after = await page.evaluate(() => window.scrollY)
  expect(Math.abs(after - before)).toBeLessThan(30)
})
