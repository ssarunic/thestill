import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import type { DriftReport } from './run'
import { resetDriftAttempts, transcriptIntervals, useDriftAlignment } from './useDriftAlignment'

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
afterEach(() => vi.unstubAllGlobals())

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
    expect(result.current).toMatchObject({ status: 'done', report })
    expect(worker.terminated).toBe(true)
    expect(reportDriftOutcome).toHaveBeenCalledTimes(1)
    const [podcast, episode, body] = reportDriftOutcome.mock.calls[0] as [string, string, { outcome: string }]
    expect([podcast, episode, body.outcome]).toEqual(['show', 'ep', 'aligned'])

    // A refetched transcript, a pause and a resume do not download the file again.
    rerender(props({ transcript: { ...transcript } }))
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('keeps the result for a reader that is opened again', () => {
    const first = renderHook(() => useDriftAlignment(props()))
    act(() => FakeWorker.instances[0].onmessage?.({ data: { type: 'done', report } }))
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
})
