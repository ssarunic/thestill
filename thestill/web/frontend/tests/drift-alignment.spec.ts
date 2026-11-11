/**
 * Spec #116 Phase 2 — the acceptance test: with a timeline map applied, a
 * click on a transcript segment lands on the same words whichever copy of
 * the audio the listener was served.
 *
 * Unlike the other specs here, the audio is real. Two copies of one
 * "episode" with different ads (tests/fixtures/drift, built by
 * make_fixture.py) are served from a second origin with CORS and Range
 * support, as a podcast host would. Everything under /api/** is still
 * stubbed. The whole chain runs in the browser: the alignment worker, the
 * service worker reading what the audio element is served, the two checks
 * that tie the map to that audio, and the engine applying it.
 *
 *   transcribed.mp3: episode 0-20 s | 6 s ad | episode 20-40 s
 *   listener.mp3:    5 s pre-roll | episode 0-20 s | 10 s ad | episode 20-40 s
 *
 * So transcript time 0-20 s plays 5 s later for this listener and
 * transcript time 26-46 s plays 9 s later.
 */
import { createServer, type Server } from 'node:http'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { AddressInfo } from 'node:net'
import { test, expect, type Page } from '@playwright/test'
import { EPISODE, EPISODE_PATH, PODCAST, mockEpisodeApi } from './episode-hero-fixture'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'drift')
const FILES: Record<string, Buffer> = {
  '/transcribed.mp3': readFileSync(join(FIXTURES, 'transcribed.mp3')),
  '/listener.mp3': readFileSync(join(FIXTURES, 'listener.mp3')),
}
const TRACK = readFileSync(join(FIXTURES, 'track.sync'))

// A podcast host: another origin, CORS for anyone, byte ranges.
let host: Server
let hostOrigin = ''

test.beforeAll(async () => {
  host = createServer((request, response) => {
    const file = FILES[(request.url ?? '').split('?')[0]]
    const cors = { 'access-control-allow-origin': '*', 'accept-ranges': 'bytes', 'content-type': 'audio/mpeg' }
    if (!file) {
      response.writeHead(404, cors).end()
      return
    }
    const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range ?? '')
    if (!range) {
      response.writeHead(200, { ...cors, 'content-length': file.length }).end(file)
      return
    }
    const start = Number(range[1])
    const end = range[2] === '' ? file.length - 1 : Math.min(Number(range[2]), file.length - 1)
    response
      .writeHead(206, {
        ...cors,
        'content-length': end - start + 1,
        'content-range': `bytes ${start}-${end}/${file.length}`,
      })
      .end(file.subarray(start, end + 1))
  })
  await new Promise<void>((resolve) => host.listen(0, '127.0.0.1', resolve))
  hostOrigin = `http://127.0.0.1:${(host.address() as AddressInfo).port}`
})

test.afterAll(async () => {
  await new Promise((resolve) => host.close(resolve))
})

function segment(id: number, start: number, end: number, text: string, kind = 'content') {
  return {
    id,
    start,
    end,
    speaker: kind === 'content' ? 'Host' : null,
    text,
    kind,
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

// The transcript of transcribed.mp3.
const TRANSCRIPT = {
  episode_id: 'ep-uuid-1',
  language: 'en',
  algorithm_version: 'test',
  cleaning: null,
  transcript_source_duration_s: 46,
  playback_time_offset_seconds: 0,
  sync_track_generation: 'e2e',
  segments: [
    segment(1, 0, 10, 'The first ten seconds of the episode.'),
    segment(2, 10, 20, 'The next ten, up to the ad break.'),
    segment(3, 20, 26, 'An ad that only the transcribed copy carries.', 'ad_break'),
    segment(4, 26, 30, 'Back from the break.'),
    segment(5, 30, 36, 'The paragraph the listener clicks.'),
    segment(6, 36, 46, 'The end of the episode.'),
  ],
}

interface Outcome {
  outcome: string
  applied: boolean
  sw_state: string | null
  verify_length_ok: boolean | null
  spans: { offset: number }[]
}

async function openEpisode(page: Page, file: 'transcribed.mp3' | 'listener.mp3') {
  const outcomes: Outcome[] = []
  await mockEpisodeApi(page, {
    audioUrl: `${hostOrigin}/${file}`,
    transcriptSegments: TRANSCRIPT,
    permissions: ['drift.align'],
    duration: 46,
  })
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/sync-track/e2e`, (route) =>
    route.fulfill({ body: TRACK, contentType: 'application/octet-stream' }),
  )
  await page.route(`**/api/podcasts/${PODCAST}/episodes/${EPISODE}/drift-outcome`, (route) => {
    outcomes.push(route.request().postDataJSON() as Outcome)
    return route.fulfill({ json: { status: 'ok', accepted: true } })
  })
  // A listener on their second visit: this host is known to allow CORS, so
  // the audio loads with crossorigin, and the service worker is installed.
  await page.addInitScript((origin) => {
    localStorage.setItem('thestill:drift:enabled', '1')
    const key = `thestill:drift:cors:http://${new URL(origin).hostname}`
    localStorage.setItem(key, JSON.stringify({ v: 'cors_ok', t: Date.now() }))
  }, hostOrigin)
  await page.goto(`${EPISODE_PATH}?view=transcript&driftdebug=1`)
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/sw.js')
    await navigator.serviceWorker.ready
    while (!navigator.serviceWorker.controller) await new Promise((resolve) => setTimeout(resolve, 50))
  })
  await page.getByRole('button', { name: /^Play episode/ }).click()
  return outcomes
}

/** Where the audio element is, in seconds of the copy it is playing. */
const elementTime = (page: Page) => page.evaluate(() => document.querySelector('video')!.currentTime)

test.describe('timeline map applied to a listener copy with other ads', () => {
  test('a segment click lands on the same words as in the transcribed copy', async ({ page }) => {
    const outcomes = await openEpisode(page, 'listener.mp3')
    const strip = page.getByTestId('drift-debug-strip')

    // The whole chain: aligned, the playing audio checked, the map in force.
    await expect(strip).toContainText('Applied: seeks, the clock and the highlighting follow this map', {
      timeout: 20_000,
    })
    await expect(strip).toContainText('the element is playing the aligned copy')
    await expect.poll(() => outcomes.length).toBeGreaterThan(0)
    const applied = outcomes.find((o) => o.applied)!
    expect(applied).toMatchObject({ outcome: 'aligned', sw_state: 'ok', verify_length_ok: true })
    expect(applied.spans.map((span) => Math.round(span.offset))).toEqual([5, 9])
    // With the map in force the drift banner has nothing left to warn about.
    await expect(page.getByText('Timestamps may have drifted')).toHaveCount(0)

    // The paragraph at 30 s of the transcript is 9 s later in this copy.
    await page.getByTestId('segment-content-5').click()
    await expect.poll(() => elementTime(page), { timeout: 5_000 }).toBeGreaterThan(38.5)
    expect(await elementTime(page)).toBeLessThan(40.5)
    // …and the reader highlights the paragraph that is being spoken.
    await expect(page.getByTestId('segment-content-5')).toHaveAttribute('data-active', 'true')

    // A paragraph before the break is 5 s later, not 9.
    await page.getByTestId('segment-content-2').click()
    await expect.poll(() => elementTime(page), { timeout: 5_000 }).toBeGreaterThan(14.5)
    expect(await elementTime(page)).toBeLessThan(16.5)
    await expect(page.getByTestId('segment-content-2')).toHaveAttribute('data-active', 'true')
  })

  test('the transcribed copy itself needs no map and seeks as before', async ({ page }) => {
    const outcomes = await openEpisode(page, 'transcribed.mp3')
    const strip = page.getByTestId('drift-debug-strip')

    await expect(strip).toContainText('Nothing to correct: this is the transcribed audio', { timeout: 20_000 })
    await expect.poll(() => outcomes.length).toBeGreaterThan(0)
    expect(outcomes[0]).toMatchObject({ outcome: 'identity', applied: false })

    await page.getByTestId('segment-content-5').click()
    await expect.poll(() => elementTime(page), { timeout: 5_000 }).toBeGreaterThan(29.5)
    expect(await elementTime(page)).toBeLessThan(31.5)
    await expect(page.getByTestId('segment-content-5')).toHaveAttribute('data-active', 'true')
  })
})
