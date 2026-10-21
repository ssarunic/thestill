/**
 * Spec #105 — the handoff from server-written HTML to the React page.
 *
 * Hermetic: the built shell is served with a server-shaped fallback and
 * data block injected at the body marker (what thestill/web/public_prerender
 * writes), and /api/** is stubbed. The page must show its content from the
 * first frame, keep exactly one h1 through the swap, and not fetch again
 * what the data block carried.
 *
 * The `@live` test compares a real server's no-JS text with the rendered
 * page (PLAYWRIGHT_BASE_URL plus PRERENDER_SAMPLE_PATHS, comma-separated).
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
const second = { ...quote, text: 'Packaging is the new bottleneck for every chip.', start_seconds: 620 }

const episode = {
  slug: 'chips',
  title: 'Chips and Cheese',
  description: 'Publisher notes about chips.',
  pub_date: '2026-10-01T00:00:00Z',
  duration_formatted: '42:00',
  image_url: null,
  explicit: false,
  episode_number: 1,
  season_number: null,
  website_url: null,
  audio_url: null,
  platform_links: [],
  has_transcript: true,
  hosts: [],
  guests: [{ name: 'Dylan Patel', type: 'person', slug: 'dylan-patel', image_url: null }],
  podcast: { slug: 'public-show', title: 'Public Show', author: null, image_url: null, apple_url: null, spotify_url: null, youtube_url: null },
}
const summary = {
  content: '## The Gist\n\nTwo hosts argue about chips.',
  more_sections: ['Timeline', 'Best Quotes'],
  language: 'en',
  canonical_language: 'en',
  available_languages: ['en'],
}
const quotes = {
  quotes: [quote, second],
  total: 2,
  page: 1,
  next_page: null,
  linked: null,
  speakers: [{ name: 'Dylan Patel', slug: 'dylan-patel', quotes: 2 }],
  mentioned: [{ name: 'Nvidia', type: 'company', slug: 'nvidia', count: 4 }],
}
const listing = { episodes: [], total: 0, offset: 0, limit: 10, next_offset: null }

const SEEDED = [
  '/api/public/podcasts/public-show/episodes/chips',
  '/api/public/podcasts/public-show/episodes/chips/summary',
  '/api/public/podcasts/public-show/episodes/chips/quotes',
  '/api/public/podcasts/public-show/episodes',
]

// Shaped as the server writes it (classes trimmed to what matters here).
const FALLBACK = `<div data-prerendered="episode"><div class="public-theme"><header class="ts-header"><div class="ts-header-inner"><a href="/" class="ts-wordmark">thestill</a></div></header><main class="ts-main" id="main"><article class="ts-container"><section class="ts-hero"><div class="ts-hero-body"><p class="ts-eyebrow"><a href="/podcasts/public-show">Public Show</a></p><h1 class="ts-episode-title">Chips and Cheese</h1></div></section><div class="ts-summary"><h2>The Gist</h2><p>Two hosts argue about chips.</p></div><figure class="ts-quote"><blockquote>\u201c${quote.text}\u201d</blockquote></figure></article></main></div></div>`

function bootstrap(): string {
  const block = {
    v: 1,
    page: 'episode',
    viewer: 'anonymous',
    queries: [
      { query: 'episode', args: ['public-show', 'chips'], data: { episode } },
      { query: 'summary', args: ['public-show', 'chips'], data: summary },
      { query: 'episodeQuotes', args: ['public-show', 'chips', null, null], data: quotes, page_param: 1 },
      { query: 'podcastEpisodes', args: ['public-show'], data: listing, page_param: 0 },
    ],
  }
  return `<script type="application/json" id="thestill-bootstrap">${JSON.stringify(block).replace(/</g, '\\u003c')}</script>`
}

async function stubApi(page: Page): Promise<string[]> {
  const calls: string[] = []
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    calls.push(url.pathname)
    if (url.pathname === '/api/auth/status')
      return route.fulfill({ json: { multi_user: true, public_pages: true, authenticated: false, email_delivery_available: false, user: null } })
    if (url.pathname === '/api/public/pageview') return route.fulfill({ status: 204 })
    const path = url.pathname.replace('/api/public', '')
    if (path === '/podcasts/public-show/episodes/chips') return route.fulfill({ json: { episode } })
    if (path === '/podcasts/public-show/episodes/chips/summary') return route.fulfill({ json: summary })
    if (path === '/podcasts/public-show/episodes/chips/quotes') return route.fulfill({ json: quotes })
    if (path === '/podcasts/public-show/episodes') return route.fulfill({ json: listing })
    return route.fulfill({ status: 404, json: { detail: 'Not found' } })
  })
  return calls
}

async function serveWritten(page: Page, path: string) {
  await page.route(`**${path}`, async (route) => {
    if (route.request().resourceType() !== 'document') return route.fallback()
    const response = await route.fetch()
    const html = (await response.text()).replace('<!--thestill:body--><!--/thestill:body-->', FALLBACK + bootstrap())
    return route.fulfill({ response, body: html, headers: { ...response.headers(), 'content-type': 'text/html; charset=utf-8' } })
  })
}

for (const viewport of [
  { name: 'phone', width: 375, height: 812 },
  { name: 'desktop', width: 1280, height: 900 },
]) {
  test(`a server-written episode hands over without a blank frame or a refetch (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const calls = await stubApi(page)
    await serveWritten(page, '/podcasts/public-show/episodes/chips')
    // Count h1s in #root on every DOM change once parsing is done (while
    // the parser streams, a half-built root is not a frame anyone sees).
    await page.addInitScript(() => {
      const w = window as unknown as { __h1: number[] }
      w.__h1 = []
      new MutationObserver(() => {
        const root = document.getElementById('root')
        if (root && document.readyState !== 'loading') w.__h1.push(root.querySelectorAll('h1').length)
      }).observe(document, { childList: true, subtree: true })
    })
    await page.goto('/podcasts/public-show/episodes/chips')
    // React's page, not the server's: only React renders "Also: …".
    await expect(page.getByText('Also: Timeline · Best Quotes')).toBeVisible()
    await expect(page.locator('[data-prerendered]')).toHaveCount(0)
    await expect(page.locator('#thestill-bootstrap')).toHaveCount(0)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Chips and Cheese')
    await expect(page.getByText('Packaging is the new bottleneck')).toBeVisible()
    await page.waitForLoadState('networkidle')
    const counts = await page.evaluate(() => (window as unknown as { __h1: number[] }).__h1)
    expect(counts.length).toBeGreaterThan(0)
    expect(counts.every((n) => n === 1)).toBe(true)
    expect(calls.filter((p) => SEEDED.includes(p))).toEqual([])
    const layoutShift = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          let total = 0
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
              if (!entry.hadRecentInput) total += entry.value
            }
          }).observe({ type: 'layout-shift', buffered: true })
          setTimeout(() => resolve(total), 200)
        }),
    )
    expect(layoutShift).toBeLessThanOrEqual(0.1)
  })
}

test('without a data block the page fetches as before', async ({ page }) => {
  const calls = await stubApi(page)
  await page.goto('/podcasts/public-show/episodes/chips')
  await expect(page.getByText('Also: Timeline · Best Quotes')).toBeVisible()
  expect(calls).toContain('/api/public/podcasts/public-show/episodes/chips')
})

test('@live server text without JavaScript is text the page shows', async ({ browser }) => {
  const paths = (process.env.PRERENDER_SAMPLE_PATHS ?? '').split(',').filter(Boolean)
  test.skip(paths.length === 0, 'set PRERENDER_SAMPLE_PATHS and PLAYWRIGHT_BASE_URL to a server with a corpus')
  const normalize = (s: string) => s.replace(/\s+/g, ' ').trim()
  for (const path of paths) {
    const noJs = await browser.newContext({ javaScriptEnabled: false })
    const plain = await noJs.newPage()
    await plain.goto(path)
    const serverText = normalize(await plain.locator('main').innerText())
    await noJs.close()
    expect(serverText.split(' ').length, path).toBeGreaterThan(100)

    const withJs = await browser.newContext()
    const rendered = await withJs.newPage()
    await rendered.goto(path)
    await rendered.waitForLoadState('networkidle')
    const pageText = normalize(await rendered.locator('body').innerText())
    await withJs.close()
    const sentences = serverText.split(/(?<=[.!?])\s+/).map((s) => s.replace(/[“”…]/g, '').trim()).filter((s) => s.length > 20)
    for (const sentence of sentences) expect(pageText.replace(/[“”]/g, ''), `${path}: ${sentence}`).toContain(sentence)
  }
})
