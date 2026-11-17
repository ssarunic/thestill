/**
 * The inbox on a phone: with a fresh briefing, three episodes in progress and
 * episodes arriving, the inbox list itself still starts above the fold.
 *
 * Hermetic: every /api/** call the page makes is stubbed.
 */
import { test, expect, type Page } from '@playwright/test'

const PHONE = { width: 393, height: 732 }
test.use({ viewport: PHONE, isMobile: true, hasTouch: true })

const NOW = '2026-11-17T09:00:00Z'

function episode(n: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `ep-${n}`,
    slug: `episode-${n}`,
    title: `Episode number ${n} with a title long enough to need truncating on a phone`,
    podcast_id: 'p1',
    audio_url: 'https://example.com/a.mp3',
    pub_date: NOW,
    state: 'summarized',
    duration: 3600,
    is_failed: false,
    image_url: null,
    ...overrides,
  }
}

const podcast = { id: 'p1', title: 'How I AI', slug: 'how-i-ai', image_url: null }

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
          is_admin: false,
        },
      },
    }),
  )
  await page.route((url) => url.pathname === '/api/inbox', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: NOW,
        items: [1, 2, 3, 4, 5, 6].map((n) => ({
          entry: {
            id: `entry-${n}`,
            user_id: 'u1',
            episode_id: `ep-${n}`,
            source: 'follow_new',
            state: 'unread',
            delivered_at: NOW,
            state_changed_at: null,
          },
          episode: episode(n),
          podcast,
          listening: null,
        })),
        count: 6,
        next_before: null,
      },
    }),
  )
  await page.route('**/api/inbox/arriving**', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: NOW,
        items: [7, 8].map((n) => ({
          episode: episode(n, { state: 'downloaded' }),
          podcast,
          active_stage: 'transcribe',
          active_status: 'processing',
        })),
        count: 2,
        total: 2,
      },
    }),
  )
  const briefing = (id: string, createdAt: string, listenedAt: string | null) => ({
    id,
    user_id: 'u1',
    cursor_from: createdAt,
    cursor_to: createdAt,
    episode_count: 9,
    script_path: null,
    audio_path: null,
    created_at: createdAt,
    listened_at: listenedAt,
    opened_at: listenedAt,
  })
  await page.route('**/api/briefings/latest**', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: NOW,
        narrations: [],
        ...briefing('b2', NOW, null),
        next_run_at: '2026-11-18T08:00:00Z',
        previous: briefing('b1', '2026-11-16T08:00:00Z', null),
        upcoming: { next_run_at: '2026-11-18T08:00:00Z', new_episode_count: 3, pending_count: 1, deadline: null },
      },
    }),
  )
  await page.route('**/api/me/listening**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/latest')) return route.fulfill({ json: { item: null } })
    return route.fulfill({
      json: {
        items: [10, 11, 12].map((n) => ({
          listening: { position_seconds: 1200, duration_seconds: 3600, completed_at: null, updated_at: NOW },
          episode: {
            id: `ep-${n}`,
            slug: `episode-${n}`,
            title: `How I fight token fraud as a solo founder, part ${n}`,
            podcast_id: 'p1',
            podcast_slug: 'how-i-ai',
            podcast_title: 'How I AI',
            audio_url: 'https://example.com/a.mp3',
            image_url: null,
            podcast_image_url: null,
            duration: 3600,
            playback: null,
          },
        })),
      },
    })
  })
}

test('the inbox list starts above the fold on a phone', async ({ page }) => {
  await mockApi(page)
  await page.goto('/inbox')
  const strip = page.getByTestId('continue-listening')
  await expect(strip).toBeVisible()
  await expect(page.getByTestId('arriving-soon')).toBeVisible()
  const firstRow = page.getByRole('link', { name: /Episode number 1 / })
  await expect(firstRow).toBeVisible()

  const box = await firstRow.boundingBox()
  expect(box).not.toBeNull()
  // The first two inbox rows fit, not just the top edge of one.
  expect(box!.y + 2 * box!.height).toBeLessThanOrEqual(PHONE.height)
  await page.screenshot({ path: test.info().outputPath('inbox-phone.png') })
})

test('Continue listening is one row that scrolls sideways', async ({ page }) => {
  await mockApi(page)
  await page.goto('/inbox')
  const strip = page.getByTestId('continue-listening')
  const cards = strip.getByRole('listitem')
  await expect(cards).toHaveCount(3)
  const tops = await Promise.all([0, 1, 2].map(async (i) => (await cards.nth(i).boundingBox())!.y))
  expect(new Set(tops.map(Math.round)).size).toBe(1)
  // The page itself never scrolls sideways.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
