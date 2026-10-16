/**
 * Spec #95 Layer B — what actually leaves the browser.
 *
 * Hermetic: /api is stubbed and /ingest (the same-origin PostHog proxy) is
 * intercepted, so these run against the real bundled posthog-js and assert
 * the privacy promises end to end: only allowlisted events, no query
 * strings or search text, nothing stored in cookies or browser storage, no
 * request to a PostHog host, and no PostHog code at all on public pages.
 */
import { gunzipSync } from 'node:zlib'
import { test, expect, type Page } from '@playwright/test'

const USER = {
  id: 'u1',
  email: 'r@example.com',
  name: 'Reader',
  picture: null,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
  region: 'gb',
  region_locked: false,
  is_admin: false,
  usage_analytics_enabled: true,
}

const ALLOWED_EVENTS = ['$pageview', '$identify', 'share', 'entity_open']

// posthog-js drops events from headless browsers as bots; look like a person.
test.use({
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
})

function decodeBatch(url: string, body: Buffer | null): unknown[] {
  if (!body) return []
  const gzipped = new URL(url).searchParams.get('compression') === 'gzip-js' || (body[0] === 0x1f && body[1] === 0x8b)
  let text = gzipped ? gunzipSync(body).toString('utf8') : body.toString('utf8')
  if (text.startsWith('data=')) text = Buffer.from(decodeURIComponent(text.slice(5)), 'base64').toString('utf8')
  const parsed = JSON.parse(text)
  return Array.isArray(parsed) ? parsed : Array.isArray(parsed.batch) ? parsed.batch : [parsed]
}

async function signedIn(page: Page, analytics: { key: string } | null) {
  const ingest: { url: string; events: unknown[] }[] = []
  const foreignHosts: string[] = []
  page.on('request', (request) => {
    const host = new URL(request.url()).host
    if (host.includes('posthog')) foreignHosts.push(host)
  })
  // posthog-js also treats navigator.webdriver and a HeadlessChrome brand
  // in navigator.userAgentData (both set by Playwright) as a bot.
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false })
    Object.defineProperty(navigator, 'userAgentData', {
      get: () => ({ brands: [{ brand: 'Chromium', version: '129' }, { brand: 'Google Chrome', version: '129' }], mobile: false, platform: 'macOS' }),
    })
  })
  await page.route('**/api/**', (route) => route.fulfill({ status: 404, json: {} }))
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        multi_user: true,
        public_pages: true,
        authenticated: true,
        email_delivery_available: false,
        usage_analytics_available: true,
        analytics,
        user: USER,
      },
    }),
  )
  await page.route('**/api/inbox**', (route) =>
    route.fulfill({ json: { status: 'ok', timestamp: '', items: [], count: 0, next_before: null } }),
  )
  await page.route('**/ingest/**', (route) => {
    const request = route.request()
    ingest.push({ url: request.url(), events: decodeBatch(request.url(), request.postDataBuffer()) })
    return route.fulfill({ status: 200, json: { status: 1 } })
  })
  return { ingest, foreignHosts }
}

test('a signed-in session sends only allowlisted events, without URLs or search text', async ({ page, context }) => {
  const { ingest, foreignHosts } = await signedIn(page, { key: 'phc_test' })
  // In-app navigation, as a reader moves around (a full reload would drop
  // the SDK's in-memory queue, which the real app never does between pages).
  const navigate = (to: string) =>
    page.evaluate((path) => {
      history.pushState({}, '', path)
      dispatchEvent(new PopStateEvent('popstate'))
    }, to)
  type Sent = { event: string; properties: Record<string, unknown> }
  const sent = () => ingest.flatMap((r) => r.events) as Sent[]
  const kinds = () => sent().filter((e) => e.event === '$pageview').map((e) => e.properties.path_kind)

  // Each page view is reported once the auth status (and with it the key)
  // has arrived, so wait for one page's view before moving to the next: on
  // a slow runner a navigation can otherwise beat the status request.
  await page.goto('/inbox')
  await expect.poll(kinds, { timeout: 15_000 }).toContain('/inbox')
  await navigate('/search?q=secretword')
  await expect.poll(kinds, { timeout: 15_000 }).toContain('/search')
  await navigate('/settings')
  await expect.poll(kinds, { timeout: 15_000 }).toContain('/settings')
  for (const e of sent()) expect(ALLOWED_EVENTS).toContain(e.event)
  for (const e of sent()) expect(e.properties.app).toBe('thestill')
  for (const e of sent()) expect(String(e.properties.$current_url ?? '')).not.toContain('?')

  const everything = JSON.stringify(ingest)
  expect(everything).not.toContain('secretword')
  expect(everything).not.toContain('?q=')
  expect(everything).not.toContain('r@example.com')
  for (const r of ingest) expect(new URL(r.url).pathname.startsWith('/ingest/')).toBe(true)
  expect(foreignHosts).toEqual([])

  const cookies = await context.cookies()
  expect(cookies.filter((c) => c.name.startsWith('ph_'))).toEqual([])
  const storageKeys = await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)])
  expect(storageKeys.filter((k) => k.includes('ph_') || k.includes('posthog'))).toEqual([])
})

test('switching usage events off in Settings stops sending and stores nothing', async ({ page }) => {
  const { ingest } = await signedIn(page, { key: 'phc_test' })
  let optedOut = false
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        multi_user: true,
        public_pages: true,
        authenticated: true,
        email_delivery_available: false,
        usage_analytics_available: true,
        analytics: optedOut ? null : { key: 'phc_test' },
        user: { ...USER, usage_analytics_enabled: !optedOut },
      },
    }),
  )
  await page.route('**/api/auth/me', (route) => {
    optedOut = true
    return route.fulfill({ json: { status: 'ok', user: { ...USER, usage_analytics_enabled: false } } })
  })
  await page.goto('/settings')
  await page.getByRole('checkbox', { name: /Help improve thestill/ }).click()
  await expect(page.getByRole('checkbox', { name: /Help improve thestill/ })).not.toBeChecked()

  const sentBefore = ingest.flatMap((r) => r.events).length
  await page.evaluate(() => {
    history.pushState({}, '', '/inbox')
    dispatchEvent(new PopStateEvent('popstate'))
  })
  await page.waitForTimeout(4_000)
  const after = (ingest.flatMap((r) => r.events) as { properties: Record<string, unknown> }[]).slice(sentBefore)
  expect(after.filter((e) => e.properties.path_kind === '/inbox')).toEqual([])
  const storageKeys = await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)])
  expect(storageKeys.filter((k) => k.includes('ph_') || k.includes('posthog'))).toEqual([])
})

test('without a key (opted out, GPC or off) the SDK is never loaded', async ({ page }) => {
  const { ingest } = await signedIn(page, null)
  const chunks: string[] = []
  page.on('response', async (response) => {
    if (response.url().endsWith('.js') && (await response.text()).includes('posthog-js')) chunks.push(response.url())
  })
  await page.goto('/inbox')
  await page.waitForTimeout(1_000)
  expect(chunks).toEqual([])
  expect(ingest).toEqual([])
})

test('public pages load no PostHog code and report views to our own server', async ({ page }) => {
  const views: unknown[] = []
  const chunks: string[] = []
  page.on('response', async (response) => {
    if (response.url().endsWith('.js') && (await response.text()).includes('posthog-js')) chunks.push(response.url())
  })
  await page.route('**/api/**', (route) => route.fulfill({ status: 404, json: {} }))
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { multi_user: true, public_pages: true, authenticated: false, user: null } }),
  )
  await page.route('**/api/public/pageview', (route) => {
    views.push(route.request().postDataJSON())
    return route.fulfill({ status: 204 })
  })
  await page.route('**/ingest/**', (route) => {
    chunks.push(`ingest:${route.request().url()}`)
    return route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/privacy')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Privacy Policy')
  await expect.poll(() => views.length).toBe(1)
  expect(views[0]).toEqual({ path: '/privacy', referrer: null })
  expect(chunks).toEqual([])
})
