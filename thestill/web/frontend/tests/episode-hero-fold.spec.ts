/**
 * Spec #76 §7 / §7.1 — the episode page on a 393 px phone.
 *
 * Hermetic (every /api/** call stubbed). Asserts the gates the spec makes
 * measurable: the tabs start inside the first viewport for a two-line title
 * and a three-line description; no pipeline/index pill above the fold; every
 * action-row control is a 44 px target at 320 px; the collapsed header pins
 * under the shell's fixed mobile header. Runs in CI; the final sign-off is
 * still a look on a real iPhone.
 */
import { test, expect } from '@playwright/test'
import { EPISODE_PATH, EPISODE_TITLE, PHONE, mockEpisodeApi } from './episode-hero-fixture'

test.describe('episode hero on a phone (spec #76)', () => {
  test.use({ viewport: PHONE, isMobile: true, hasTouch: true })

  test('tabs start inside the first viewport with a two-line title and clamped description', async ({ page }) => {
    await mockEpisodeApi(page)
    await page.goto(EPISODE_PATH)

    const title = page.getByRole('heading', { name: EPISODE_TITLE })
    await expect(title).toBeVisible()
    const titleBox = (await title.boundingBox())!
    // 22 px / 1.2 → one line is ~26 px; the gate must be exercised by a wrap.
    expect(titleBox.height).toBeGreaterThanOrEqual(50)
    await expect(page.getByRole('button', { name: 'More' })).toBeVisible()

    const tab = page.getByRole('button', { name: 'Summary' })
    await expect(tab).toBeVisible()
    const tabBox = (await tab.boundingBox())!
    expect(tabBox.y + tabBox.height).toBeLessThanOrEqual(PHONE.height)
  })

  test('no pipeline or index state above the fold, duration on the primary action', async ({ page }) => {
    await mockEpisodeApi(page)
    await page.goto(EPISODE_PATH)
    await expect(page.getByRole('button', { name: 'Play episode, 58 min' })).toBeVisible()
    await expect(page.getByText('Ready', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Indexed', { exact: true })).toHaveCount(0)
    const eyebrow = page.getByText('S3 E12').locator('..')
    await expect(eyebrow).toBeVisible()
    await expect(eyebrow).toContainText('Explicit')
  })

  test('collapsed header pins under the 56 px mobile header and clears on scroll back', async ({ page }) => {
    await mockEpisodeApi(page)
    await page.goto(EPISODE_PATH)
    await expect(page.getByRole('heading', { name: EPISODE_TITLE })).toBeVisible()
    await expect(page.getByTestId('collapsed-episode-bar')).toHaveCount(0)

    await page.evaluate(() => window.scrollTo(0, 900))
    const bar = page.getByTestId('collapsed-episode-bar')
    await expect(bar).toBeVisible()
    await expect(bar).toContainText(EPISODE_TITLE)
    const box = (await bar.boundingBox())!
    expect(Math.round(box.y)).toBe(56)
    const play = bar.getByRole('button', { name: 'Play' })
    const playBox = (await play.boundingBox())!
    expect(playBox.width).toBeGreaterThanOrEqual(44)
    expect(playBox.height).toBeGreaterThanOrEqual(44)

    await page.evaluate(() => window.scrollTo(0, 0))
    await expect(page.getByTestId('collapsed-episode-bar')).toHaveCount(0)
  })

  test('People and Information render below the tabs', async ({ page }) => {
    await mockEpisodeApi(page)
    await page.goto(EPISODE_PATH)
    const people = page.getByRole('region', { name: 'People' })
    await expect(people).toContainText('Ed Elson')
    await expect(people).toContainText('Jim VandeHei')
    await expect(people).not.toContainText('SPEAKER_02')
    const info = page.getByRole('region', { name: 'Information' })
    await expect(info).toContainText('Prof G Media')
    await expect(info).toContainText('58 min 25 s')
    await expect(info).toContainText('profgmedia.com')
    const tabBox = (await page.getByRole('button', { name: 'Summary' }).boundingBox())!
    const peopleBox = (await people.boundingBox())!
    expect(peopleBox.y).toBeGreaterThan(tabBox.y)
  })
})

test.describe('leaving and returning', () => {
  test.use({ viewport: PHONE, isMobile: true, hasTouch: true })

  test('Back from a People link returns to the reading position', async ({ page }) => {
    await mockEpisodeApi(page)
    // A host entity so the People row has a link out (registered after the
    // fixture, so it takes precedence over its empty entities stub).
    await page.route('**/api/episodes/*/entities*', (route) =>
      route.fulfill({
        json: {
          status: 'ok',
          episode_id: 'ep-uuid-1',
          podcast_id: 'p-1',
          entities: [
            {
              entity: { id: 'ent-ed', type: 'person', canonical_name: 'Ed Elson', wikidata_qid: null },
              mention_count: 3,
              first_mention_ms: 0,
              speaker_kind: 'host',
              salience: 0.9,
              mentions: [],
            },
          ],
        },
      }),
    )
    await page.route('**/api/entities/**', (route) => route.fulfill({ status: 404, json: { detail: 'not found' } }))
    await page.goto(EPISODE_PATH)
    await expect(page.getByRole('heading', { name: EPISODE_TITLE })).toBeVisible()

    const people = page.getByRole('region', { name: 'People' })
    await people.scrollIntoViewIfNeeded()
    const before = await page.evaluate(() => window.scrollY)
    expect(before).toBeGreaterThan(300)

    await people.getByRole('link', { name: 'Ed Elson' }).click()
    await expect(page).toHaveURL(/\/entities\//)
    await page.goBack()
    await expect(page.getByRole('heading', { name: EPISODE_TITLE })).toBeVisible()
    await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 3000 }).toBeGreaterThan(before - 60)
  })
})

test.describe('action row at the narrowest supported width', () => {
  test.use({ viewport: { width: 320, height: 732 }, isMobile: true, hasTouch: true })

  test('all four slots are 44 px targets and nothing overflows', async ({ page }) => {
    await mockEpisodeApi(page)
    await page.goto(EPISODE_PATH)
    // Primary + Watch video + Share + Show notes: the full slot set the
    // deferred overflow menu (spec §3.2) would have to handle.
    const controls = [
      page.getByRole('button', { name: 'Play episode, 58 min' }),
      page.getByRole('button', { name: 'Watch video' }),
      page.getByRole('button', { name: /^(share|copy link)$/i }),
      page.getByRole('link', { name: 'Show notes' }),
    ]
    for (const control of controls) {
      await expect(control).toBeVisible()
      const box = (await control.boundingBox())!
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
      // Buttons and links alike show the hand (index.css base rule).
      await expect(control).toHaveCSS('cursor', 'pointer')
    }
    const boxes = await Promise.all(controls.map((c) => c.boundingBox()))
    // Single row (centres aligned; the 48 px primary and 44 px icons have
    // different top edges), in order, entirely inside the viewport.
    const centres = boxes.map((b) => Math.round(b!.y + b!.height / 2))
    expect(Math.max(...centres) - Math.min(...centres)).toBeLessThanOrEqual(1)
    for (let i = 1; i < boxes.length; i += 1) expect(boxes[i]!.x).toBeGreaterThan(boxes[i - 1]!.x)
    expect(boxes[3]!.x + boxes[3]!.width).toBeLessThanOrEqual(320)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
