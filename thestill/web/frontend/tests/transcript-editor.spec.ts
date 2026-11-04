/**
 * Spec #110 §5 — the transcript editor screen, hermetic. Every /api/** call
 * is stubbed; the op log lives in this file. A session holding
 * ``transcript.edit`` opens the editor from the ⋯ menu, names the unnamed
 * voice from the speakers column, sees the transcript re-render with the
 * name, and finishes back on the episode page; a session without the
 * permission never sees the entry point and the route sends it back.
 */
import { expect, test, type Page, type Route } from '@playwright/test'
import { EPISODE_PATH, mockEpisodeApi } from './episode-hero-fixture'

const STATUS_USER = {
  id: 'u1',
  email: 'test@example.com',
  name: 'Test',
  picture: null,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
  region: 'gb',
  region_locked: false,
  is_admin: true,
}

async function mockAuth(page: Page, permissions: string[]) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: { multi_user: true, authenticated: true, email_delivery_available: false, user: STATUS_USER, permissions },
    }),
  )
}

function segment(id: number, start: number, speaker: string, text: string) {
  return {
    id,
    start,
    end: start + 4,
    speaker,
    text,
    kind: 'content',
    sponsor: null,
    source_segment_ids: [id],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

/** The op log and the transcript it produces, mutated by the stubbed POSTs. */
function editorBackend(page: Page) {
  const edits: Array<Record<string, unknown>> = []
  const posted: Array<Record<string, unknown>> = []
  let finished: Record<string, unknown> | null = null
  let adSpeaker = 'SPEAKER_02'

  const state = () => ({
    status: 'ok',
    episode_id: 'ep-uuid-1',
    last_seq: edits.length,
    raw_sha256: 'a'.repeat(64),
    overlay_failed: false,
    busy: false,
    edits,
    conflicts: [],
    speakers: [
      { label: 'SPEAKER_00', name: 'Ed Elson', entity_id: null, segment_ids: [1] },
      { label: 'SPEAKER_01', name: 'Jim VandeHei', entity_id: null, segment_ids: [2] },
      { label: 'SPEAKER_02', name: adSpeaker, entity_id: null, segment_ids: [3] },
    ],
    summary_stale_edits: edits.length,
    algorithm_version: 'v1',
    local_audio: false,
  })

  const register = async () => {
    // Registered after mockEpisodeApi: Playwright matches the newest route first.
    await page.route(`**/api/podcasts/prof-g-markets/episodes/why-nobody-trusts-the-news/transcript`, (route) =>
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
              segment(3, 8, adSpeaker, 'Ad read.'),
            ],
          },
        },
      }),
    )
    await page.route('**/transcript/words', (route) => route.fulfill({ status: 404, json: { detail: 'no words' } }))
    await page.route('**/api/episodes/ep-uuid-1/transcript/edits/finish', (route: Route) => {
      finished = route.request().postDataJSON()
      route.fulfill({ json: { status: 'ok', session_ops: edits.length, refresh: 'queued', task_id: 't-1' } })
    })
    await page.route('**/api/episodes/ep-uuid-1/transcript/edits/people*', (route) =>
      route.fulfill({ json: { status: 'ok', people: [] } }),
    )
    await page.route('**/api/episodes/ep-uuid-1/transcript/edits', (route: Route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON()
        posted.push(body)
        const applied = (body.ops as Array<Record<string, unknown>>).map((op, index) => {
          if (op.op === 'speaker.rename_label') adSpeaker = String((op.args as { name: string }).name)
          return {
            id: `e-${edits.length + index + 1}`,
            seq: edits.length + index + 1,
            op: op.op,
            anchor: op.anchor,
            args: op.args,
            before: {},
            reverts: null,
            session_id: body.session_id,
            report_id: body.report_id ?? null,
            actor_user_id: 'u1',
            actor_kind: 'web',
            created_at: '2026-11-02T12:00:00+00:00',
            status: 'applied',
            conflict: null,
          }
        })
        edits.push(...applied)
        route.fulfill({ json: { status: 'ok', applied, last_seq: edits.length } })
        return
      }
      route.fulfill({ json: state() })
    })
  }
  return { register, posted, get finished() { return finished } }
}

test('an editor opens the screen, names an unnamed voice and finishes', async ({ page }) => {
  await mockEpisodeApi(page)
  await mockAuth(page, ['transcript.edit'])
  const backend = editorBackend(page)
  await backend.register()

  await page.goto(`${EPISODE_PATH}?view=transcript`)
  await expect(page.getByTestId('segment-content-3')).toContainText('SPEAKER_02')

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('button', { name: 'Edit transcript' }).click()
  await expect(page).toHaveURL(/\/edit$/)
  await expect(page.getByTestId('transcript-editor-page')).toBeVisible()
  // No episode chrome: the screen is the bar, the tools, the turns and the dock.
  await expect(page.getByRole('button', { name: 'More actions' })).toHaveCount(0)
  await expect(page.getByTestId('transport-dock')).toBeVisible()
  await expect(page.getByTestId('lane-timeline')).toBeVisible()
  await expect(page.getByTestId('lane-block-3')).toBeVisible()

  // The speakers column lists the leak; focusing it keeps the other turns in view.
  const row = page.getByTestId('speaker-row-SPEAKER_02')
  await expect(row).toContainText('Unnamed')
  await row.click()
  await expect(page.getByTestId('editor-row-1')).toBeVisible()
  await expect(page.getByTestId('editor-row-1')).toHaveClass(/opacity-40/)
  await expect(page.getByTestId('editor-row-3')).not.toHaveClass(/opacity-40/)

  await row.getByRole('button', { name: 'Actions for SPEAKER_02' }).click()
  await page.getByRole('button', { name: 'Name or link…' }).click()
  await page.getByRole('textbox', { name: 'Speaker name' }).fill('Ad Narrator')
  await page.getByRole('button', { name: 'Save' }).click()

  await expect.poll(() => backend.posted.length).toBe(1)
  expect(backend.posted[0]).toMatchObject({
    base_seq: 0,
    ops: [{ op: 'speaker.rename_label', anchor: { label: 'SPEAKER_02' }, args: { name: 'Ad Narrator', entity_id: null } }],
  })
  await expect(page.getByTestId('editor-row-3')).toContainText('Ad Narrator')
  await expect(page.getByTestId('transcript-editor-bar')).toContainText('1 edit')

  await page.getByRole('button', { name: 'Finish editing' }).click()
  await expect.poll(() => backend.finished).not.toBeNull()
  await expect(page).toHaveURL(/view=transcript/)
  await expect(page.getByText(/People, quotes and search are refreshing/)).toBeVisible()
})

test('a reader without the permission has no entry point and the route sends them back', async ({ page }) => {
  await mockEpisodeApi(page)
  await mockAuth(page, [])
  let editorStateRequests = 0
  await page.route('**/api/episodes/ep-uuid-1/transcript/edits', (route) => {
    editorStateRequests += 1
    route.fulfill({ status: 403, json: { detail: 'Permission required: transcript.edit' } })
  })

  await page.goto(`${EPISODE_PATH}?view=transcript`)
  await expect(page.getByTestId('segment-content-1')).toBeVisible()
  await page.getByRole('button', { name: 'More actions' }).click()
  await expect(page.getByRole('button', { name: 'Edit transcript' })).toHaveCount(0)

  await page.goto(`${EPISODE_PATH}/edit`)
  await expect(page).toHaveURL(/view=transcript/)
  await expect(page.getByTestId('transcript-editor-page')).toHaveCount(0)
  expect(editorStateRequests).toBe(0)
})
