import { afterEach, describe, expect, it, vi } from 'vitest'
import { chunked, fromBase64, render, vectorCase } from '../test/driftStreams'
import { runDriftAlignment } from './run'

const TRACK_URL = '/api/podcasts/show/episodes/ep/sync-track/vectors'
const AUDIO_URL = 'https://feeds.example/redirect/episode.mp3'

function streamOf(bytes: Uint8Array, chunkSize = 50_000): ReadableStream<Uint8Array> {
  const chunks = [...chunked(bytes, chunkSize)]
  return new ReadableStream({
    pull(controller) {
      const next = chunks.shift()
      if (next) controller.enqueue(next.slice())
      else controller.close()
    },
  })
}

interface Served {
  track?: Uint8Array | number
  audio?: Uint8Array | number | 'blocked'
  finalUrl?: string
}

function serve({ track, audio, finalUrl = 'https://cdn.host.example/a/b.mp3?session=1' }: Served) {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      if (url === TRACK_URL) {
        if (typeof track === 'number') return new Response('', { status: track })
        return new Response(track as Uint8Array)
      }
      if (audio === 'blocked') throw new TypeError('Failed to fetch')
      if (typeof audio === 'number') return new Response('', { status: audio })
      const bytes = audio as Uint8Array
      const response = new Response(streamOf(bytes), { headers: { 'content-length': String(bytes.length) } })
      Object.defineProperty(response, 'url', { value: finalUrl })
      return response
    }),
  )
  return calls
}

const input = (segments = [{ startS: 0, endS: 1000, isAd: false }]) => ({
  trackUrl: TRACK_URL,
  audioUrl: AUDIO_URL,
  segments,
})

afterEach(() => vi.unstubAllGlobals())

describe('runDriftAlignment', () => {
  it('finds the offsets of a copy with other ads and reports what it cost', async () => {
    const testCase = vectorCase('pre-roll added and a longer mid-roll')
    const audio = render(testCase.other)
    const calls = serve({ track: fromBase64(testCase.sync_track), audio })
    const progress: number[] = []
    // The transcript: 20 s of episode, a 6 s ad break, 20 s of episode.
    const segments = [
      { startS: 0, endS: 20, isAd: false },
      { startS: 20, endS: 26, isAd: true },
      { startS: 26, endS: 46, isAd: false },
    ]

    const report = await runDriftAlignment(input(segments), { onProgress: (p) => progress.push(p.fetchedBytes) })

    expect(report.outcome).toBe('aligned')
    expect(report.generation).toBe('vectors')
    expect(report.spans.map((s) => Math.round(s.offsetS))).toEqual([8, 17])
    expect(report.gaps.filter((g) => g.kind === 'confirmed')).toHaveLength(1)
    expect(report.coverage).toBeGreaterThan(0.9)
    expect(report.anchorsHit).toBe(testCase.expected.anchors_hit)
    expect(report.anchorsInlier).toBe(testCase.expected.anchors_inlier)
    expect(report.fetchedBytes).toBe(audio.length)
    expect(report.audioBytes).toBe(testCase.track_stream_bytes)
    // The host that served the bytes after redirects, never the URL.
    expect(report.audioHost).toBe('cdn.host.example')
    expect(report.error).toBeNull()
    expect(progress[progress.length - 1]).toBe(audio.length)

    // The audio host is a third party: anonymous CORS request, no cookies.
    const audioCall = calls.find((c) => c.url === AUDIO_URL)!
    expect(audioCall.init).toMatchObject({ mode: 'cors', credentials: 'omit' })
    expect(calls.find((c) => c.url === TRACK_URL)!.init).toMatchObject({ credentials: 'include' })
  })

  it('calls the same bytes an identity', async () => {
    const testCase = vectorCase('identical copy')
    serve({ track: fromBase64(testCase.sync_track), audio: render(testCase.other) })
    const report = await runDriftAlignment(input([{ startS: 0, endS: 29, isAd: false }]))
    expect(report.outcome).toBe('identity')
    expect(report.spans).toHaveLength(1)
  })

  it('does not call an unshifted copy of a different length an identity', async () => {
    // Same episode, but the listener's copy has a trailing ad.
    const testCase = vectorCase('identical copy')
    const longer = render([...testCase.other, { kind: 'frames', seed: 99, count: 200 }])
    serve({ track: fromBase64(testCase.sync_track), audio: longer })
    const report = await runDriftAlignment(input([{ startS: 0, endS: 29, isAd: false }]))
    expect(report.outcome).toBe('aligned')
  })

  it('reports low confidence when the copy was re-encoded', async () => {
    const testCase = vectorCase('re-encoded copy')
    serve({ track: fromBase64(testCase.sync_track), audio: render(testCase.other) })
    const report = await runDriftAlignment(input([{ startS: 0, endS: 20, isAd: false }]))
    expect(report.outcome).toBe('low_confidence')
    expect(report.spans).toEqual([])
    expect(report.coverage).toBe(0)
  })

  it('reports no_cors when the page may not read the audio', async () => {
    serve({ track: fromBase64(vectorCase('identical copy').sync_track), audio: 'blocked' })
    const report = await runDriftAlignment(input())
    expect(report.outcome).toBe('no_cors')
    expect(report.audioHost).toBe('feeds.example')
    expect(report.fetchedBytes).toBeNull()
  })

  it('reports an audio response that is not MP3 as an unsupported codec', async () => {
    serve({ track: fromBase64(vectorCase('identical copy').sync_track), audio: new Uint8Array(40_000).fill(7) })
    const report = await runDriftAlignment(input())
    expect(report).toMatchObject({ outcome: 'unsupported_codec', error: 'audio_has_no_mp3_frames' })
  })

  it.each([
    [{ track: 404 }, 'track_http_404'],
    [{ track: new Uint8Array([1, 2, 3]) }, 'track_corrupt'],
    [{ track: 'ok', audio: 403 }, 'audio_http_403'],
  ] as const)('turns a failed fetch into an error outcome: %#', async (served, error) => {
    const track = served.track === 'ok' ? fromBase64(vectorCase('identical copy').sync_track) : served.track
    serve({ ...served, track } as Served)
    const report = await runDriftAlignment(input())
    expect(report).toMatchObject({ outcome: 'error', error })
  })

  it('rejects when aborted, so a cancelled attempt reports nothing', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        controller.abort()
        throw new DOMException('aborted', 'AbortError')
      }),
    )
    await expect(runDriftAlignment(input(), { signal: controller.signal })).rejects.toThrow('aborted')
  })
})
