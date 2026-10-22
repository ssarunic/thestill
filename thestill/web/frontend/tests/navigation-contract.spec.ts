/**
 * Navigation contract (docs/code-guidelines.md "Navigation invariants"):
 * on every page, scrolling down, following a link out, and pressing Back
 * returns to the same offset; the destination itself starts at the top.
 *
 * Hermetic — every /api/** call is stubbed. One table row per page; add a
 * row when you add a page or a new kind of link out of one. `/top` is
 * covered in more depth (filters too) by scroll-restoration.spec.ts.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE_PATH, mockEpisodeApi, PODCAST } from './episode-hero-fixture'

const NOW = '2026-09-07T00:00:00Z'

function episodeRow(i: number) {
  return {
    id: `ep-${i}`,
    podcast_index: 1,
    podcast_slug: PODCAST,
    podcast_title: 'Prof G Markets',
    episode_index: i,
    title: `Episode ${i}: a title long enough to wrap on a phone and fill the row`,
    slug: `episode-${i}`,
    description: 'd',
    pub_date: `2026-08-${String((i % 28) + 1).padStart(2, '0')}T07:00:00Z`,
    audio_url: `https://example.com/${i}.mp3`,
    duration: 3600,
    duration_formatted: '1:00:00',
    external_id: `x-${i}`,
    state: 'summarized',
    transcript_available: true,
    summary_available: true,
    image_url: null,
    summary_preview: 'A preview of the gist, long enough to take a line or two on a phone.',
  }
}

function paged<T>(key: string, rows: T[]) {
  return { status: 'ok', timestamp: NOW, [key]: rows, count: rows.length, total: rows.length, offset: 0, limit: rows.length, has_more: false, next_offset: null }
}

async function mockLists(page: Page) {
  // Playwright gives precedence to the LAST registered route, so the
  // catch-all goes first and the specific stubs override it. Unknown
  // endpoints 404 (as with no stub at all): a 200 with an empty body would
  // trip hooks that trust the response shape.
  await page.route('**/api/**', (route) => route.fulfill({ status: 404, json: { detail: 'not stubbed' } }))
  await mockEpisodeApi(page)
  const episodes = Array.from({ length: 40 }, (_, i) => episodeRow(i + 1))
  await page.route(/\/api\/podcasts\/[^/]+\/episodes(\?.*)?$/, (route) => route.fulfill({ json: paged('episodes', episodes) }))
  await page.route(/\/api\/podcasts\/[^/?]+(\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: NOW,
        podcast: {
          id: 'p-1', index: 1, title: 'Prof G Markets', description: 'Markets, explained.', rss_url: 'https://example.com/rss',
          slug: PODCAST, image_url: null, primary_category: 'Business', primary_subcategory: null, secondary_category: null,
          secondary_subcategory: null, last_processed: NOW, episodes_count: 40, episodes_processed: 40, is_following: true,
          author: 'Prof G Media', explicit: false, website_url: null, is_complete: false, copyright: null,
        },
      },
    }),
  )
  await page.route(/\/api\/episodes(\?.*)?$/, (route) => route.fulfill({ json: paged('episodes', episodes) }))
  await page.route(/\/api\/commands\/processing(\?.*)?$/, (route) => route.fulfill({ json: { status: 'ok', tasks: [] } }))
  const briefings = Array.from({ length: 40 }, (_, i) => ({
    id: `b-${i}`, user_id: 'u1', cursor_from: NOW, cursor_to: NOW, episode_count: 3, script_path: null, audio_path: null,
    created_at: `2026-08-${String((i % 28) + 1).padStart(2, '0')}T07:00:00Z`, listened_at: i % 2 ? NOW : null,
  }))
  await page.route(/\/api\/briefings(\?.*)?$/, (route) => route.fulfill({ json: paged('briefings', briefings) }))
  await page.route('**/api/briefings/**', (route) => route.fulfill({ status: 404, json: { detail: 'not found' } }))
  await page.route('**/api/entities/**', (route) => route.fulfill({ status: 404, json: { detail: 'not found' } }))
}

// Links that need setup a table row cannot express live in their own spec but
// still honour the contract:
//   - Now Playing sheet → "Open transcript here" (spec #72): now-playing-sheet.spec.ts
// Spec #104 — a multi-user admin session and the console's endpoints.
async function mockAdminConsole(page: Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        multi_user: true,
        authenticated: true,
        email_delivery_available: false,
        user: { id: 'admin', email: 'op@example.com', name: 'Op', picture: null, created_at: NOW, last_login_at: NOW, region: null, region_locked: false, is_admin: true },
      },
    }),
  )
  const users = Array.from({ length: 40 }, (_, i) => ({
    id: `u-${i + 1}`, email: `user${i + 1}@example.com`, name: `User ${i + 1}`, picture: null, created_at: NOW,
    last_login_at: NOW, signup_source: null, is_admin: false, status: 'active', status_reason: null,
    status_changed_at: null, status_changed_by: null, follows: 40 - i, suspended_follows: 0,
    feeds_brought_in_24h: 0, feeds_brought_in_total: 0, follows_24h: 0, imports_24h: 0, translations_24h: 0, quota_hits_24h: 0,
  }))
  await page.route('**/api/admin/settings', (route) =>
    route.fulfill({ json: { status: 'ok', settings: [], non_default: {}, alert_channels: ['log'] } }),
  )
  await page.route(/\/api\/admin\/users(\?.*)?$/, (route) => route.fulfill({ json: paged('users', users) }))
  await page.route(/\/api\/admin\/users\/u-\d+$/, (route) =>
    route.fulfill({
      json: {
        status: 'ok', user: users[29], usage_24h: {}, usage_7d: {}, daily_7d: {}, follows: [],
        quota_limits: { imports_per_day: 5 }, quota_overrides: {}, history: [],
      },
    }),
  )
}

const ROUTES: { name: string; path: string; ready: (page: Page) => Promise<void>; link: (page: Page) => ReturnType<Page['locator']> }[] = [
  {
    name: 'Podcast detail → episode',
    path: `/podcasts/${PODCAST}`,
    ready: async (page) => expect(page.getByRole('heading', { name: 'Prof G Markets' })).toBeVisible(),
    link: (page) => page.getByRole('link', { name: /Episode 20:/ }),
  },
  {
    name: 'Episodes → episode',
    path: '/episodes',
    ready: async (page) => expect(page.getByRole('link', { name: /Episode 1:/ }).first()).toBeVisible(),
    link: (page) => page.getByRole('link', { name: /Episode 20:/ }),
  },
  {
    name: 'Briefings → briefing',
    path: '/briefings',
    ready: async (page) => expect(page.getByRole('link', { name: /2026/ }).first()).toBeVisible(),
    link: (page) => page.getByRole('link', { name: /2026/ }).nth(20),
  },
  {
    name: 'Episode → person',
    path: EPISODE_PATH,
    ready: async (page) => expect(page.getByRole('region', { name: 'People' })).toBeVisible(),
    link: (page) => page.getByRole('region', { name: 'People' }).getByRole('link').first(),
  },
  {
    // Spec #104 — the admin console's user list (multi-user admin session).
    name: 'Admin users → user',
    path: '/admin/users?sort=follows',
    ready: async (page) => expect(page.getByRole('link', { name: 'user1@example.com' })).toBeVisible(),
    link: (page) => page.getByRole('link', { name: 'user30@example.com' }),
  },
]

test.describe('navigation contract', () => {
  test.use({ viewport: { width: 393, height: 732 }, isMobile: true, hasTouch: true })

  for (const route of ROUTES) {
    test(`${route.name}: Back restores the scroll position, the destination starts at the top`, async ({ page }) => {
      await mockLists(page)
      if (route.path === EPISODE_PATH) {
        await page.route('**/api/episodes/*/entities*', (r) =>
          r.fulfill({
            json: {
              status: 'ok', episode_id: 'ep-uuid-1', podcast_id: 'p-1',
              entities: [{ entity: { id: 'ent-ed', type: 'person', canonical_name: 'Ed Elson', wikidata_qid: null }, mention_count: 3, first_mention_ms: 0, speaker_kind: 'host', salience: 0.9, mentions: [] }],
            },
          }),
        )
      }
      if (route.name === 'Admin users → user') await mockAdminConsole(page)
      await page.goto(route.path)
      await route.ready(page)

      const link = route.link(page)
      await link.scrollIntoViewIfNeeded()
      // The link is somewhere down the page, so this is a real offset.
      const before = await page.evaluate(() => window.scrollY)
      expect(before).toBeGreaterThan(200)

      // Keep the document tall across the navigation. A destination that
      // renders short while it loads lets the browser clamp the offset to 0,
      // which hid a missing scroll-to-top for every signed-in page until the
      // hook moved out of Layout (beneath <Routes location> a PUSH reads as
      // POP).
      await page.addStyleTag({ content: 'body { min-height: 20000px }' })
      await link.click()
      await expect(page).not.toHaveURL(new RegExp(`${route.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`))
      // Destination starts at the top.
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5)

      await page.goBack()
      await route.ready(page)
      await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 3000 }).toBeGreaterThan(before - 60)
    })
  }
})
