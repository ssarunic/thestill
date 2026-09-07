/**
 * Spec #76 — stubbed episode used by the hero gate specs. Hermetic: every
 * /api/** call is intercepted. The title is long enough to wrap to two lines
 * at 393 px and the description long enough to clamp to three, because those
 * are the inputs the fold budget (§7.1) is sensitive to.
 */
import type { Page } from '@playwright/test'

export const PODCAST = 'prof-g-markets'
export const EPISODE = 'why-nobody-trusts-the-news'
export const EPISODE_PATH = `/podcasts/${PODCAST}/episodes/${EPISODE}`
export const EPISODE_TITLE = 'Why Nobody Trusts the News — And How to Fix It, With Jim VandeHei'

// 852 − ~120 px of Safari chrome (§2): the first viewport a listener sees.
export const PHONE = { width: 393, height: 732 }

const DESCRIPTION =
  'Ed Elson sits down with Jim VandeHei, co-founder and CEO of Axios, to talk about why trust in ' +
  'the news keeps falling, what the business model of a modern newsroom actually looks like, and ' +
  'whether the incentives of platforms can ever be squared with accuracy. They also cover the ' +
  'week in markets: rate expectations, the earnings that mattered, and one chart that explains ' +
  'the rest. As always, a listener question closes the show.'

function segment(id: number, start: number, speaker: string, text: string) {
  return {
    id,
    start,
    end: start + 4,
    speaker,
    text,
    kind: 'content',
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

export async function mockEpisodeApi(page: Page) {
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
  // Most specific first (see reader-live-refresh.spec.ts).
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/summary*`, (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-09-07T00:00:00Z',
        episode_id: 'ep-uuid-1',
        episode_title: EPISODE_TITLE,
        content: '## Key takeaways\n\nTrust follows incentives.',
        available: true,
        citations: null,
        language: 'en',
        podcast_language: 'en',
        canonical_language: 'en',
        available_languages: ['en'],
      },
    }),
  )
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
          segments: [
            segment(1, 0, 'Ed Elson', 'Welcome back to Prof G Markets.'),
            segment(2, 4, 'Jim VandeHei', 'Thanks for having me.'),
            segment(3, 8, 'SPEAKER_02', 'Ad read.'),
          ],
        },
      },
    }),
  )
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}`, (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-09-07T00:00:00Z',
        episode: {
          id: 'ep-uuid-1',
          podcast_id: 'p-1',
          podcast_slug: PODCAST,
          podcast_title: 'Prof G Markets',
          podcast_author: 'Prof G Media',
          podcast_language: 'en',
          origin: 'feed',
          import_kind: null,
          title: EPISODE_TITLE,
          description: DESCRIPTION,
          description_html: `<p>${DESCRIPTION}</p>`,
          slug: EPISODE,
          pub_date: '2026-09-06T07:00:00Z',
          audio_url: 'https://example.com/a.mp3',
          duration: 3505,
          duration_formatted: '58:25',
          external_id: 'ext-1',
          state: 'summarized',
          has_transcript: true,
          has_summary: true,
          image_url: null,
          podcast_image_url: null,
          explicit: true,
          episode_type: 'full',
          episode_number: 12,
          season_number: 3,
          website_url: 'https://www.profgmedia.com/episodes/12',
          is_failed: false,
        },
      },
    }),
  )
  await page.route('**/api/commands/episode/*/tasks', (route) =>
    route.fulfill({ json: { status: 'ok', episode_id: 'ep-uuid-1', tasks: [] } }),
  )
  await page.route('**/api/episodes/*/entities*', (route) => route.fulfill({ json: { status: 'ok', entities: [] } }))
  await page.route('**/api/episodes/*/related*', (route) => route.fulfill({ json: { status: 'ok', episodes: [] } }))
  await page.route('**/api/inbox/**', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/briefings/**', (route) => route.fulfill({ status: 404, json: {} }))
}
