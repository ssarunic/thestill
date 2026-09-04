/**
 * Spec #73 — list rows on a 393 px phone.
 *
 * Hermetic: every /api/** call is stubbed. Asserts the two numbers the spec
 * promises for Top podcasts: the title column is wide enough to read
 * (≥ 160 px) and the Follow control is a real 44 px target. Also checks the
 * group runs full-bleed (row content edge at the page's 16 px padding).
 */
import { test, expect, type Page } from '@playwright/test'

test.use({ viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true })

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
  await page.route('**/api/top-podcasts*', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-09-04T00:00:00Z',
        region: 'gb',
        available_regions: ['gb', 'us'],
        available_categories: ['Comedy', 'History'],
        user_region: 'gb',
        count: 2,
        top_podcasts: [
          {
            rank: 1,
            name: 'The Rest Is History',
            artist: 'Goalhanger',
            rss_url: 'https://example.com/trih',
            apple_url: 'https://podcasts.apple.com/gb/podcast/trih',
            youtube_url: null,
            category: 'History',
            source_genre: null,
            is_following: true,
            podcast_slug: 'the-rest-is-history',
            image_url: null,
          },
          {
            rank: 2,
            name: 'The Romesh Ranganathan Show',
            artist: 'Ranga Bee Productions',
            rss_url: 'https://example.com/rrs',
            apple_url: 'https://podcasts.apple.com/gb/podcast/rrs',
            youtube_url: null,
            category: 'Comedy',
            source_genre: null,
            is_following: false,
            podcast_slug: 'the-romesh-ranganathan-show',
            image_url: null,
          },
        ],
      },
    }),
  )
}

test('Top podcasts row gives the title room and a 44 px Follow target', async ({ page }) => {
  await mockApi(page)
  await page.goto('/top')

  const title = page.getByRole('link', { name: 'Open The Romesh Ranganathan Show' })
  await expect(title).toBeVisible()
  const titleBox = await title.boundingBox()
  expect(titleBox).not.toBeNull()
  expect(titleBox!.width).toBeGreaterThanOrEqual(160)

  const follow = page.getByRole('button', { name: 'Follow', exact: true })
  const followBox = await follow.boundingBox()
  expect(followBox!.height).toBeGreaterThanOrEqual(44)
  expect(followBox!.width).toBeGreaterThanOrEqual(44)

  // Full-bleed: the list edge is the screen edge, and the row content sits at
  // the page's 16 px padding rather than inset behind a card border.
  const list = page.getByRole('list')
  const listBox = await list.boundingBox()
  expect(listBox!.x).toBeLessThanOrEqual(0.5)
  expect(listBox!.width).toBeGreaterThanOrEqual(392)
  const rank = page.getByText('2', { exact: true })
  const rankBox = await rank.boundingBox()
  expect(rankBox!.x).toBeGreaterThanOrEqual(16)
  expect(rankBox!.x + rankBox!.width).toBeLessThanOrEqual(16 + 24)

  // The Apple attribution link is not in the phone row.
  await expect(page.getByRole('link', { name: 'Open in Apple Podcasts' })).toHaveCount(0)
})
