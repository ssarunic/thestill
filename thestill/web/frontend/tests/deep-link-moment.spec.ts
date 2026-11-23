/**
 * A link to a moment (`#t=` from a shared quote, `?t=` from inside the app)
 * opened cold by a signed-in reader: it lands on the passage in the
 * transcript, marks it, and offers "Play from". Nothing plays until that
 * tap — a browser refuses audio the visitor did not ask for.
 */
import { test, expect, type Page } from '@playwright/test'
import { EPISODE_PATH, mockEpisodeApi } from './episode-hero-fixture'

function segment(id: number) {
  return {
    id,
    start: id * 10,
    end: id * 10 + 9,
    speaker: id % 2 ? 'Ed Elson' : 'Jim VandeHei',
    text: `Passage ${id}. ` + 'A long stretch of conversation that makes the transcript far taller than the screen. '.repeat(3),
    kind: 'content',
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

// Playwright's own page scripts run "with a user gesture", which would make
// every visit look like a click inside the app. A cold visit has none.
async function openCold(page: Page, url: string) {
  await page.addInitScript(() =>
    Object.defineProperty(Navigator.prototype, 'userActivation', {
      configurable: true,
      get: () => ({ hasBeenActive: false, isActive: false }),
    }),
  )
  await page.goto(url)
}

async function mockLongEpisode(page: Page) {
  await mockEpisodeApi(page, {
    transcriptSegments: {
      episode_id: 'ep-uuid-1',
      language: 'en',
      transcript_source_duration_s: 3505,
      playback_time_offset_seconds: 0,
      segments: Array.from({ length: 200 }, (_, i) => segment(i + 1)),
    },
  })
}

for (const [name, suffix] of [
  ['fragment', '#t=1004'],
  ['query string', '?t=1004'],
] as const) {
  test(`a cold link to a moment (${name}) lands on its passage and waits for Play from`, async ({ page }) => {
    await mockLongEpisode(page)
    await openCold(page, `${EPISODE_PATH}${suffix}`)

    // 1004 s falls in passage 100 (1000–1009 s): on screen and marked.
    const passage = page.getByTestId('segment-content-100')
    await expect(passage).toBeInViewport()
    await expect(passage).toHaveAttribute('data-linked', 'true')
    const play = page.getByRole('button', { name: 'Play from 16:44' })
    await expect(play).toBeInViewport()

    // Nothing was loaded into the player on arrival.
    const player = page.getByRole('region', { name: 'Audio player' })
    await expect(player).toHaveCount(0)

    await play.click()
    await expect(player).toBeVisible()
    await expect(page.getByTestId('linked-moment')).toHaveCount(0)
  })
}

test('Summary leaves the moment behind instead of bouncing back to the transcript', async ({ page }) => {
  await mockLongEpisode(page)
  await openCold(page, `${EPISODE_PATH}?t=1004`)
  await expect(page.getByTestId('segment-content-100')).toBeInViewport()

  await page.getByRole('button', { name: 'Summary', exact: true }).click()
  await expect(page.getByText('Trust follows incentives.')).toBeVisible()
  await expect(page).toHaveURL(new RegExp(`${EPISODE_PATH}$`))
})
