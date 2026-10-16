/**
 * Spec #94 — signed-out public pages.
 *
 * Hermetic: /api/auth/status says "multi-user, not signed in" and every
 * /api/public/** call is stubbed. Any other /api request is recorded and
 * fails the test — a public page that reaches for a signed-in endpoint
 * would 401 in production and leak the private app's shape.
 */
import { test, expect, type Page } from '@playwright/test'

const quote = {
  text: 'The CUDA moat is real, but it is changing quickly.',
  start_seconds: 460,
  speaker: { name: 'Dylan Patel', slug: 'dylan-patel' },
  about: { name: 'Nvidia', type: 'company', slug: 'nvidia', id: 'company:nvidia' },
  episode: { slug: 'chips', title: 'Chips and Cheese', pub_date: '2026-10-01T00:00:00Z', image_url: null },
  podcast: { slug: 'public-show', title: 'Public Show' },
}

const episodeItem = {
  slug: 'chips',
  title: 'Chips and Cheese',
  pub_date: '2026-10-01T00:00:00Z',
  duration_formatted: '42:00',
  image_url: null,
  summary_preview: 'Two hosts argue about chips.',
  guests: [{ name: 'Dylan Patel', type: 'person', slug: 'dylan-patel' }],
  podcast: { slug: 'public-show', title: 'Public Show', image_url: null },
}

const entityPayload = {
  entity: { name: 'Dylan Patel', type: 'person', slug: 'dylan-patel', description: null, wikidata_qid: null },
  enrichment: null,
  stats: { shows: 2, episodes: 5, since: '2026-01-01T00:00:00Z', last_30_days: 1 },
  activity: { start: '2026-04-01T00:00:00Z', weeks: Array(26).fill(0) },
  roles: { hosts: [], recurring: [], guest_episodes: [] },
  most_discussed_on: [],
  related: [],
  featured_quote: quote,
  topics: [{ ...quote.about, quotes: 1 }],
  speakers: [],
  quote_counts: { said: 1, about: 0 },
  indexable: false,
}

async function mockPublicApi(page: Page): Promise<string[]> {
  const privateCalls: string[] = []
  // Registered first, so the specific routes below take precedence.
  await page.route('**/api/**', (route) => {
    privateCalls.push(new URL(route.request().url()).pathname)
    return route.fulfill({ status: 401, json: { detail: 'Not authenticated' } })
  })
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { multi_user: true, public_pages: true, authenticated: false, email_delivery_available: false, user: null } }),
  )
  await page.route('**/api/public/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/public', '')
    if (path === '/recent-summaries') return route.fulfill({ json: { episodes: [episodeItem] } })
    if (path === '/who-said-what')
      return route.fulfill({ json: { entity: null, days: 30, shows: 0, speakers: 0, quotes: [] } })
    if (path === '/podcasts/public-show/episodes/chips')
      return route.fulfill({
        json: {
          episode: {
            ...episodeItem,
            description: null,
            explicit: false,
            episode_number: 1,
            season_number: null,
            website_url: null,
            audio_url: null,
            platform_links: [],
            has_transcript: true,
            hosts: [],
            podcast: { slug: 'public-show', title: 'Public Show', author: null, image_url: null, apple_url: null, spotify_url: null, youtube_url: null },
          },
        },
      })
    if (path === '/podcasts/public-show/episodes/chips/summary')
      return route.fulfill({
        json: { content: '## The Gist\n\nTwo hosts argue about chips.', language: 'en', canonical_language: 'en', available_languages: ['en'] },
      })
    if (path === '/podcasts/public-show/episodes/chips/quotes')
      return route.fulfill({
        json: { quotes: [quote], total: 1, linked: url.searchParams.get('q') === '460' ? quote : null, mentioned: [] },
      })
    if (path === '/podcasts/public-show/episodes') return route.fulfill({ json: { episodes: [], total: 0, next_offset: null } })
    if (path === '/entities/person/dylan-patel') return route.fulfill({ json: entityPayload })
    if (path.startsWith('/entities/person/dylan-patel/quotes'))
      return route.fulfill({ json: { quotes: [quote], total: 1, page: 1, next_page: null } })
    return route.fulfill({ status: 404, json: { detail: 'Not found' } })
  })
  return privateCalls
}

test('landing renders the latest summary without signed-in calls', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Read your podcasts')
  await expect(page.getByText('Chips and Cheese').first()).toBeVisible()
  expect(privateCalls).toEqual([])
})

test('episode page shows the summary, keeps the transcript behind sign-in, and lands on a linked quote', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.goto('/podcasts/public-show/episodes/chips?t=460')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Chips and Cheese')
  await expect(page.getByText('Linked quote')).toBeVisible()
  await expect(page.getByText('one sign-in away')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Read the transcript' })).toHaveAttribute('href', /^\/login\?.*intent=transcript/)
  expect(privateCalls).toEqual([])
})

test('person page shows what they said', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.goto('/entities/person/dylan-patel')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Dylan Patel')
  await expect(page.getByText('The CUDA moat is real').first()).toBeVisible()
  expect(privateCalls).toEqual([])
})

test('a private path asks for sign-in and remembers where to return', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.goto('/inbox')
  await expect(page).toHaveURL(/\/login\?next=%2Finbox$/)
  expect(privateCalls).toEqual([])
})

test('an unknown public page is a 404 page, not a crash', async ({ page }) => {
  await mockPublicApi(page)
  await page.goto('/no/such/page')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText("This page isn't here")
  await page.goto('/podcasts/public-show/episodes/missing')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText("This episode isn't summarised yet")
})

test('phones keep the way to Top podcasts', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await mockPublicApi(page)
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Top podcasts' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
})

test('a signed-in reader can still open Terms and Privacy', async ({ page }) => {
  await page.route('**/api/**', (route) => route.fulfill({ status: 404, json: {} }))
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        multi_user: true,
        public_pages: true,
        authenticated: true,
        email_delivery_available: false,
        user: { id: 'u1', email: 'r@example.com', name: 'Reader', picture: null, created_at: '2026-01-01T00:00:00Z', last_login_at: null, region: 'gb', region_locked: false, is_admin: false },
      },
    }),
  )
  await page.goto('/privacy')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Privacy Policy')
})

test('with public pages off, a signed-out visitor gets the sign-in wall', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { multi_user: true, public_pages: false, authenticated: false, email_delivery_available: false, user: null } }),
  )
  await page.goto('/podcasts/public-show/episodes/chips')
  await expect(page).toHaveURL(/\/login/)
  expect(privateCalls).toEqual([])
})

test('every sign-in goes through the sign-in page and back (spec #102)', async ({ page }) => {
  const privateCalls = await mockPublicApi(page)
  await page.goto('/podcasts/public-show/episodes/chips')
  await page.getByRole('banner').getByRole('link', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/login\?next=%2Fpodcasts%2Fpublic-show%2Fepisodes%2Fchips$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Sign in to thestill')
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible()
  await page.getByRole('link', { name: 'Not now' }).click()
  await expect(page).toHaveURL(/\/podcasts\/public-show\/episodes\/chips$/)

  // Transcript is a normal tab; signed out it asks for sign-in and comes
  // back to the transcript view.
  await page.getByRole('tab', { name: 'Transcript' }).click()
  await expect(page).toHaveURL(/\/login\?next=%2Fpodcasts%2Fpublic-show%2Fepisodes%2Fchips%3Fview%3Dtranscript&intent=transcript$/)
  await expect(page.getByText('Sign in to read the full transcript')).toBeVisible()
  expect(privateCalls).toEqual([])
})
