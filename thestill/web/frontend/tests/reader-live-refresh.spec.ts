/**
 * Spec #68 — the episode reader must catch up on its own as the pipeline
 * advances, with no reload and no user interaction.
 *
 * This is the check the unit and jsdom-integration tests cannot make: it runs
 * the real built bundle in a real Chromium, with real timers, and asserts on
 * what is actually painted. Every /api/** call is intercepted so the *ordering*
 * that produced the original bug can be scripted exactly — in particular, the
 * task list is empty for the whole run, so no task-completion edge is ever
 * available to observe. On `main` the summary never appears here.
 *
 * Runs against PLAYWRIGHT_BASE_URL (default http://localhost:5173); point it
 * at the FastAPI server (http://127.0.0.1:8000) to exercise the built bundle.
 */
import { test, expect, type Page } from '@playwright/test'

const PODCAST = 'the-show'
const EPISODE = 'ep-1'
const READER_PATH = `/podcasts/${PODCAST}/episodes/${EPISODE}`

const SUMMARY_MARKDOWN = '## Key takeaways\n\nThe reader caught up on its own.'
const SUMMARY_MARKER = 'The reader caught up on its own.'
const PENDING_MARKER = 'Summarization in progress'

/** Server-side truth, flipped mid-test to simulate the pipeline advancing. */
type PipelineState = {
  state: string
  hasSummary: boolean
  summaryAvailable: boolean
}

function episodePayload(pipeline: PipelineState) {
  return {
    status: 'ok',
    timestamp: '2026-08-03T00:00:00Z',
    episode: {
      id: 'ep-uuid-1',
      podcast_id: 'p-1',
      podcast_slug: PODCAST,
      podcast_title: 'The Show',
      title: 'A Test Episode',
      description: '',
      description_html: '',
      slug: EPISODE,
      pub_date: '2026-08-01T00:00:00Z',
      audio_url: 'https://example.com/a.mp3',
      duration: 1800,
      duration_formatted: '30:00',
      external_id: 'ext-1',
      state: pipeline.state,
      has_transcript: true,
      has_summary: pipeline.hasSummary,
      image_url: null,
      podcast_image_url: null,
      is_failed: false,
    },
  }
}

function summaryPayload(pipeline: PipelineState) {
  return {
    status: 'ok',
    timestamp: '2026-08-03T00:00:00Z',
    episode_id: 'ep-uuid-1',
    episode_title: 'A Test Episode',
    content: pipeline.summaryAvailable ? SUMMARY_MARKDOWN : '',
    available: pipeline.summaryAvailable,
    citations: null,
    language: 'en',
    podcast_language: 'en',
    canonical_language: 'en',
    available_languages: ['en'],
  }
}

async function mockApi(page: Page, pipeline: PipelineState, counters: Record<string, number>) {
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

  // Ordered most-specific first: Playwright matches routes in registration
  // order, and the episode route below would otherwise swallow these.
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/summary*`, (route) => {
    counters.summary = (counters.summary ?? 0) + 1
    route.fulfill({ json: summaryPayload(pipeline) })
  })

  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/transcript*`, (route) => {
    counters.transcript = (counters.transcript ?? 0) + 1
    route.fulfill({
      json: {
        status: 'ok',
        timestamp: '2026-08-03T00:00:00Z',
        content: 'Transcript body.',
        available: true,
        transcript_type: 'cleaned',
      },
    })
  })

  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}`, (route) => {
    counters.episode = (counters.episode ?? 0) + 1
    route.fulfill({ json: episodePayload(pipeline) })
  })

  // The task list stays EMPTY for the entire run. This is the point of the
  // fixture: the client never gets a "task completed" edge to observe.
  await page.route('**/api/commands/episode/*/tasks', (route) => {
    counters.tasks = (counters.tasks ?? 0) + 1
    route.fulfill({ json: { status: 'ok', episode_id: 'ep-uuid-1', tasks: [] } })
  })

  // Everything else the reader touches — kept boring so it can't interfere.
  await page.route('**/api/episodes/*/entities*', (route) =>
    route.fulfill({ json: { status: 'ok', entities: [] } }),
  )
  await page.route('**/api/episodes/*/related*', (route) =>
    route.fulfill({ json: { status: 'ok', episodes: [] } }),
  )
  await page.route('**/api/inbox/**', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/briefings/**', (route) => route.fulfill({ status: 404, json: {} }))
}

test('summary appears without a reload once the pipeline produces it', async ({ page }) => {
  const pipeline: PipelineState = {
    state: 'cleaned',
    hasSummary: false,
    summaryAvailable: false,
  }
  const counters: Record<string, number> = {}
  await mockApi(page, pipeline, counters)

  await page.goto(READER_PATH)

  // Mid-pipeline: the summary pane shows progress, not content.
  await expect(page.getByText(PENDING_MARKER)).toBeVisible()
  await expect(page.getByText(SUMMARY_MARKER)).toHaveCount(0)

  // Summarization completes server-side. Nothing is clicked, nothing is
  // reloaded, and the task list never reports anything.
  pipeline.state = 'summarized'
  pipeline.hasSummary = true
  pipeline.summaryAvailable = true

  // The 5s episode poll observes the new level and the reconciliation refetches
  // the summary. Generous timeout: this asserts it happens on its own, not how
  // fast.
  await expect(page.getByText(SUMMARY_MARKER)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(PENDING_MARKER)).toHaveCount(0)
})

test('a settled reader stops polling the episode', async ({ page }) => {
  test.setTimeout(90_000)
  const pipeline: PipelineState = {
    state: 'summarized',
    hasSummary: true,
    summaryAvailable: true,
  }
  const counters: Record<string, number> = {}
  await mockApi(page, pipeline, counters)

  await page.goto(READER_PATH)
  await expect(page.getByText(SUMMARY_MARKER)).toBeVisible()

  // Let it settle, then confirm it goes quiet. Without the terminal gate this
  // would keep ticking every 5s forever — ~17k requests/day for an open tab.
  await page.waitForTimeout(8_000)
  const settledEpisodeCalls = counters.episode ?? 0

  await page.waitForTimeout(20_000)
  const delta = (counters.episode ?? 0) - settledEpisodeCalls

  // Asserted as a rate, not as zero. `refetchOnWindowFocus` is the *designed*
  // reactivation path (see D1), and Playwright's own focus changes can legally
  // trigger it — demanding zero would forbid the mechanism this feature relies
  // on to recover. A live 5s poll would add ~4 over this window; the gate is
  // working iff it adds at most one.
  expect(delta).toBeLessThanOrEqual(1)
})

test('task polling survives an empty list and stops only when terminal', async ({ page }) => {
  test.setTimeout(90_000)
  // Non-terminal + permanently empty task list: the poll must NOT latch off
  // (that is the original F1 bug), and must keep a slow probe running so
  // externally queued work is still discoverable.
  const pipeline: PipelineState = {
    state: 'cleaned',
    hasSummary: false,
    summaryAvailable: false,
  }
  const counters: Record<string, number> = {}
  await mockApi(page, pipeline, counters)

  await page.goto(READER_PATH)
  await expect(page.getByText(PENDING_MARKER)).toBeVisible()

  // Past the 4-tick grace period the cadence drops to the 15s idle tier, but
  // polling must continue — on `main` this latches off after the first empty
  // response and never resumes. Window sized for two idle ticks plus slack so
  // a slow cold page load cannot make it look like a latch.
  await page.waitForTimeout(40_000)
  expect(counters.tasks ?? 0).toBeGreaterThanOrEqual(2)
})
