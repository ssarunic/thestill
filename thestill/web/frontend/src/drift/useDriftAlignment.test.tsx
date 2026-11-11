import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import type { DriftReport } from './run'
import { getCorsVerdict, setCorsVerdict, beginAudioLoad, setCrossOriginLoadingEnabled } from './corsVerdict'
import { recordMediaReport, resetDriftServiceWorkerClient } from './swClient'
import { checkPlay, resetDriftAttempts, transcriptIntervals, useDriftAlignment } from './useDriftAlignment'

const reportDriftOutcome = vi.fn<(...args: unknown[]) => Promise<void>>(async () => {})
vi.mock('../api/client', () => ({ reportDriftOutcome: (...args: unknown[]) => reportDriftOutcome(...args) }))

const player = {
  track: null as null | { episodeId: string; audioUrl: string; playback?: unknown },
  isPlaying: false,
  activeEngine: 'native',
  activeRendition: 'audio',
}
vi.mock('../contexts/PlayerContext', () => ({ usePlayer: () => player }))

class FakeWorker {
  static instances: FakeWorker[] = []
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  posted: unknown[] = []
  terminated = false
  constructor() {
    FakeWorker.instances.push(this)
  }
  postMessage(message: unknown) {
    this.posted.push(message)
  }
  terminate() {
    this.terminated = true
  }
}

const transcript = {
  episode_id: 'ep-1',
  playback_time_offset_seconds: 2,
  sync_track_generation: 'gen1',
  segments: [
    { id: 0, start: 0, end: 10, kind: 'content' },
    { id: 1, start: 10, end: 40, kind: 'ad_break' },
  ],
} as unknown as AnnotatedTranscriptDump

const report: DriftReport = {
  outcome: 'aligned',
  generation: 'gen1',
  spans: [{ srcStartS: 0, srcEndS: 10, offsetS: 8 }],
  gaps: [],
  gapsConfirmedS: 0,
  unresolvedS: 0,
  listenerAdsS: 8,
  coverage: 1,
  anchorsTotal: 10,
  anchorsHit: 10,
  anchorsInlier: 10,
  audioHost: 'cdn.example',
  audioBytes: 1,
  fetchedBytes: 2,
  fetchMs: 3,
  fitMs: 4,
  error: null,
  blocks: Uint32Array.from([11, 22, 33, 44]),
}

const props = (overrides: Partial<Parameters<typeof useDriftAlignment>[0]> = {}) => ({
  enabled: true,
  podcastSlug: 'show',
  episodeSlug: 'ep',
  episodeId: 'ep-1',
  transcript,
  ...overrides,
})

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  resetDriftServiceWorkerClient()
  setCrossOriginLoadingEnabled(true)
  FakeWorker.instances = []
  reportDriftOutcome.mockClear()
  resetDriftAttempts()
  vi.stubGlobal('Worker', FakeWorker)
  Object.assign(player, {
    track: { episodeId: 'ep-1', audioUrl: 'https://host.example/a.mp3' },
    isPlaying: true,
    activeEngine: 'native',
    activeRendition: 'audio',
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useDriftAlignment', () => {
  it('starts one attempt when this episode’s audio plays, and reports the outcome', () => {
    const { result, rerender } = renderHook((p) => useDriftAlignment(p), { initialProps: props() })

    expect(FakeWorker.instances).toHaveLength(1)
    const worker = FakeWorker.instances[0]
    expect(worker.posted[0]).toEqual({
      type: 'start',
      input: {
        trackUrl: '/api/podcasts/show/episodes/ep/sync-track/gen1',
        audioUrl: 'https://host.example/a.mp3',
        segments: transcriptIntervals(transcript),
      },
    })
    expect(result.current.status).toBe('running')

    act(() => worker.onmessage?.({ data: { type: 'progress', progress: { fetchedBytes: 5, totalBytes: 10 } } }))
    expect(result.current.progress).toEqual({ fetchedBytes: 5, totalBytes: 10 })

    act(() => worker.onmessage?.({ data: { type: 'done', report } }))
    expect(result.current).toMatchObject({ status: 'done', report, check: null })
    expect(worker.terminated).toBe(true)
    // The alignment's fetch worked, so this chain may load with crossorigin next time.
    expect(getCorsVerdict('https://host.example/b.mp3')).toBe('cors_ok')
    // The report waits a moment for the service worker's view of the play.
    expect(reportDriftOutcome).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(1500))
    expect(result.current.check).toEqual({ swState: 'unsupported', verification: null })
    expect(reportDriftOutcome).toHaveBeenCalledTimes(1)
    const [podcast, episode, body] = reportDriftOutcome.mock.calls[0] as [
      string,
      string,
      { outcome: string; sw_state: string; verify_length_ok: boolean | null },
    ]
    expect([podcast, episode, body.outcome]).toEqual(['show', 'ep', 'aligned'])
    expect([body.sw_state, body.verify_length_ok]).toEqual(['unsupported', null])

    // A refetched transcript, a pause and a resume do not download the file again.
    rerender(props({ transcript: { ...transcript } }))
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('keeps the result for a reader that is opened again', () => {
    const first = renderHook(() => useDriftAlignment(props()))
    act(() => FakeWorker.instances[0].onmessage?.({ data: { type: 'done', report } }))
    act(() => vi.advanceTimersByTime(1500))
    first.unmount()

    const second = renderHook(() => useDriftAlignment(props()))
    expect(second.result.current.report).toEqual(report)
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it.each([
    ['the permission is not held', () => props({ enabled: false })],
    ['the transcript names no sync track', () => props({ transcript: { ...transcript, sync_track_generation: null } })],
    ['another episode is playing', () => (Object.assign(player, { track: { episodeId: 'other', audioUrl: 'x' } }), props())],
    ['nothing has been played yet', () => (Object.assign(player, { isPlaying: false }), props())],
    ['the YouTube player is active', () => (Object.assign(player, { activeEngine: 'youtube' }), props())],
    ['the video rendition is playing', () => (Object.assign(player, { activeRendition: 'video' }), props())],
    [
      'the audio is an HLS stream',
      () => (
        Object.assign(player, {
          track: {
            episodeId: 'ep-1',
            audioUrl: 'x',
            playback: { kind: 'audio', audio: { url: 'https://h/x.m3u8', mime_type: 'application/vnd.apple.mpegurl' } },
          },
        }),
        props()
      ),
    ],
  ])('does nothing when %s', (_name, build) => {
    const { result } = renderHook(() => useDriftAlignment(build()))
    expect(FakeWorker.instances).toHaveLength(0)
    expect(result.current.status).toBe('idle')
  })

  it('does nothing on a connection that asks to save data', () => {
    vi.stubGlobal('navigator', { ...navigator, userAgent: navigator.userAgent, connection: { saveData: true } })
    renderHook(() => useDriftAlignment(props()))
    expect(FakeWorker.instances).toHaveLength(0)
  })

  it('puts transcript segments on the transcribed copy’s timeline', () => {
    expect(transcriptIntervals(transcript)).toEqual([
      { startS: 2, endS: 12, isAd: false },
      { startS: 12, endS: 42, isAd: true },
    ])
  })

  it('remembers that a host refused the fetch', () => {
    renderHook(() => useDriftAlignment(props()))
    act(() =>
      FakeWorker.instances[0].onmessage?.({
        data: { type: 'done', report: { ...report, outcome: 'no_cors', fetchedBytes: null, blocks: null } },
      }),
    )
    expect(getCorsVerdict('https://host.example/a.mp3')).toBe('no_cors')
  })
})

describe('checkPlay', () => {
  const AUDIO = 'https://host.example/a.mp3'
  const seen = (overrides: object = {}) => ({
    url: AUDIO,
    range: 'bytes=0-',
    status: 206,
    opaque: false,
    contentLength: 2,
    blockIndex: 0,
    crc: 11,
    adler: 22,
    t: Date.now() + 1,
    ...overrides,
  })
  const controlled = (controller: object | null) =>
    vi.stubGlobal('navigator', { userAgent: 'x', serviceWorker: { controller, addEventListener: vi.fn() } })

  beforeEach(() => {
    vi.useRealTimers()
    localStorage.clear()
    resetDriftServiceWorkerClient()
    setCrossOriginLoadingEnabled(true)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('says so where there is no service worker', () => {
    vi.stubGlobal('navigator', { userAgent: 'x' })
    expect(checkPlay(AUDIO, report)).toEqual({ swState: 'unsupported', verification: null })
  })

  it('cannot check an element that loaded without crossorigin', () => {
    controlled({})
    beginAudioLoad(AUDIO, true) // no verdict yet: a plain load
    expect(checkPlay(AUDIO, report).swState).toBe('element_not_cors')
  })

  it('tells a worker that saw nothing from one not yet in control', () => {
    setCorsVerdict(AUDIO, 'cors_ok')
    beginAudioLoad(AUDIO, true)
    controlled({})
    expect(checkPlay(AUDIO, report).swState).toBe('no_requests')
    controlled(null)
    expect(checkPlay(AUDIO, report).swState).toBe('not_controlling')
  })

  it('verifies the play from what the worker read', () => {
    setCorsVerdict(AUDIO, 'cors_ok')
    beginAudioLoad(AUDIO, true)
    controlled({})
    recordMediaReport(seen())
    // After the redirect the browser may ask the final host directly.
    recordMediaReport(seen({ url: 'https://cdn.example/x.mp3?session=1', blockIndex: 1, crc: 33, adler: 44 }))
    recordMediaReport(seen({ url: 'https://elsewhere.example/other.mp3', crc: 99 }))
    expect(checkPlay(AUDIO, report)).toEqual({
      swState: 'ok',
      verification: { lengthOk: true, rangesChecked: 2, rangesMatched: 2, verdict: 'verified' },
    })
  })

  it('finds the element playing another copy', () => {
    setCorsVerdict(AUDIO, 'cors_ok')
    beginAudioLoad(AUDIO, true)
    controlled({})
    recordMediaReport(seen({ crc: 12345 }))
    expect(checkPlay(AUDIO, report).verification?.verdict).toBe('mismatch')
  })

  it('reports responses it could not read', () => {
    setCorsVerdict(AUDIO, 'cors_ok')
    beginAudioLoad(AUDIO, true)
    controlled({})
    recordMediaReport(seen({ opaque: true, status: 0, blockIndex: null, crc: null, adler: null, contentLength: null }))
    expect(checkPlay(AUDIO, report).swState).toBe('opaque')
  })
})
