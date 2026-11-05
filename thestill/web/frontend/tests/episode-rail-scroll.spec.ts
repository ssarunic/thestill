/**
 * The desktop entity rail scrolls on its own. A rail taller than the screen
 * used to be `sticky` with no height cap, so its bottom ("Related episodes")
 * only came into view after the whole transcript had scrolled past. Now the
 * frame is capped at the visible height (above the mini player, below the
 * collapsed episode bar) and scrolls independently.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE, EPISODE_PATH, PODCAST, mockEpisodeApi } from './episode-hero-fixture'

const DESKTOP = { width: 1280, height: 720 }

function entity(i: number, type: 'person' | 'company' | 'topic') {
  return {
    entity: { id: `${type}-${i}`, type, canonical_name: `${type} number ${i}`, wikidata_qid: null },
    mention_count: 3,
    first_mention_ms: i * 60_000,
    speaker_kind: 'unknown',
    salience: 1 - i / 100,
    mentions: [],
  }
}

function segment(id: number) {
  return {
    id,
    start: id * 10,
    end: id * 10 + 9,
    speaker: id % 2 ? 'Ed Elson' : 'Jim VandeHei',
    text: 'A long stretch of conversation that makes the transcript far taller than the screen. '.repeat(3),
    kind: 'content',
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

async function mockTallRail(page: Page) {
  await mockEpisodeApi(page)
  // Later routes win: a long transcript, a crowded rail, five related episodes.
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/transcript*`, (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-09-07T00:00:00Z',
        content: '',
        available: true,
        transcript_type: 'cleaned',
        segments: {
          episode_id: 'ep-uuid-1',
          language: 'en',
          transcript_source_duration_s: 3505,
          playback_time_offset_seconds: 0,
          segments: Array.from({ length: 200 }, (_, i) => segment(i + 1)),
        },
      },
    }),
  )
  const entities = [
    ...Array.from({ length: 20 }, (_, i) => entity(i, 'person')),
    ...Array.from({ length: 20 }, (_, i) => entity(i, 'company')),
    ...Array.from({ length: 20 }, (_, i) => entity(i, 'topic')),
  ]
  await page.route('**/api/episodes/*/entities*', (route) => route.fulfill({ json: { status: 'ok', entities } }))
  await page.route('**/api/search/related*', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        episodes: Array.from({ length: 5 }, (_, i) => ({
          episode_id: `rel-${i}`,
          podcast_id: 'p-1',
          podcast_slug: PODCAST,
          episode_slug: `related-${i}`,
          podcast_title: 'Prof G Markets',
          episode_title: `Related episode ${i}`,
          published_at: '2026-09-01T07:00:00Z',
          image_url: null,
          score: 0.9,
        })),
      },
    }),
  )
}

test.describe('episode entity rail (desktop)', () => {
  test.use({ viewport: DESKTOP })

  test('a rail taller than the screen scrolls on its own, without scrolling the transcript', async ({ page }) => {
    await mockTallRail(page)
    await page.goto(`${EPISODE_PATH}?view=transcript`)

    const frame = page.getByTestId('entity-rail-scroll')
    await expect(frame.getByText('person number 0')).toBeVisible()
    // Make it taller still: every section expanded.
    const showAll = frame.getByRole('button', { name: /Show all/ })
    while ((await showAll.count()) > 0) await showAll.first().click()

    // Mid-transcript: the frame is pinned and fits on screen.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 3))
    await expect(page.getByTestId('entity-rail-more-below')).toBeVisible()
    const box = await frame.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.y).toBeGreaterThanOrEqual(56) // below the collapsed episode bar
    expect(box!.y + box!.height).toBeLessThanOrEqual(DESKTOP.height)

    // Scrolling the rail reaches its bottom; the page stays where it was.
    const pageY = await page.evaluate(() => window.scrollY)
    await frame.evaluate((el) => el.scrollTo(0, el.scrollHeight))
    const related = frame.getByText('Related episode 4')
    await expect(related).toBeInViewport()
    await expect(page.getByTestId('entity-rail-more-below')).toHaveCount(0)
    expect(await page.evaluate(() => window.scrollY)).toBe(pageY)
  })
})
