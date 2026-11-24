import { expect, test } from '@playwright/test'
import { EPISODE, EPISODE_PATH, PODCAST, mockEpisodeApi } from './episode-hero-fixture'

// Spec #114 Rule A: a signed-in reader shares a quote from the transcript,
// but only from a turn that holds a quote that is already public.

const QUOTE = 'The best teams change their minds fastest when the evidence changes.'

function segment(id: number, start: number, end: number, text: string) {
  return {
    id,
    start,
    end,
    speaker: id === 2 ? 'Jim VandeHei' : 'Scott Galloway',
    text,
    kind: 'content',
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

const TRANSCRIPT = {
  episode_id: 'ep-uuid-1',
  language: 'en',
  algorithm_version: 'test',
  cleaning: null,
  transcript_source_duration_s: 120,
  playback_time_offset_seconds: 0,
  segments: [
    segment(1, 0, 30, 'Welcome back to the show, everyone.'),
    segment(2, 30, 60, `We talked about this before. ${QUOTE} That is the whole point.`),
    segment(3, 60, 90, 'And with that, we will take a short break.'),
  ],
}

// A 1×1 PNG standing in for the server-rendered card.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
)

test('a signed-in reader shares a public quote from its turn and copies the short link', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await mockEpisodeApi(page, { transcriptSegments: TRANSCRIPT })
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/quote-shares`, (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        shares: [{ id: 'k7m2xq3dpa', segment_id: 2, text: QUOTE, start_seconds: 30, speaker: 'Jim VandeHei' }],
      },
    }),
  )
  await page.route('**/q/k7m2xq3dpa.png', (route) => route.fulfill({ body: PNG, contentType: 'image/png' }))

  await page.goto(`${EPISODE_PATH}?view=transcript`)
  await expect(page.getByTestId('segment-content-2')).toBeVisible()
  // Only the turn with the public quote offers a share.
  await expect(page.getByTestId('quote-share-2')).toBeVisible()
  await expect(page.getByTestId('quote-share-1')).toHaveCount(0)
  await expect(page.getByTestId('quote-share-3')).toHaveCount(0)

  await page.getByRole('button', { name: 'Share a quote from Jim VandeHei' }).click()
  const dialog = page.getByRole('dialog', { name: 'Share this quote' })
  await expect(dialog.locator('img')).toHaveAttribute('src', '/q/k7m2xq3dpa.png')
  // Opening the share did not seek or start playback.
  await expect(page.getByRole('region', { name: 'Audio player' })).toHaveCount(0)

  await dialog.getByRole('button', { name: 'Copy link' }).click()
  await expect(page.getByText('Quote link copied')).toBeVisible()
  await expect(dialog).toHaveCount(0)
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`${new URL(page.url()).origin}/q/k7m2xq3dpa`)
})
