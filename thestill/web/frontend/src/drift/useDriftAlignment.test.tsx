import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import type { DriftReport } from './run'
import { getCorsVerdict, setCorsVerdict, beginAudioLoad, setCrossOriginLoadingEnabled } from './corsVerdict'
import { isTimelineExpected, resetTimelineExpectations } from './expectation'
import { recordMediaReport, resetDriftServiceWorkerClient } from './swClient'
import { checkPlay, resetDriftAttempts, transcriptIntervals, useDriftAlignment } from './useDriftAlignment'

const reportDriftOutcome = vi.fn<(...args: unknown[]) => Promise<void>>(async () => {})
vi.mock('../api/client', () => ({ reportDriftOutcome: (...args: unknown[]) => reportDriftOutcome(...args) }))

const player = {
  track: null as null | { episodeId: string; audioUrl: string; playback?: unknown },
  isPlaying: false,
  activeEngine: 'native',
  activeRendition: 'audio',
  applyTimeline: vi.fn<(...args: unknown[]) => boolean>(() => true),
  releaseTimelineHold: vi.fn<(...args: unknown[]) => void>(),
  // False: nothing is being held (the listener pressed Play, not a paragraph).
  reloadHeldForCheck: vi.fn<(...args: unknown[]) => boolean>(() => false),
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
  trackDurationS: 20,
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
  player.applyTimeline.mockClear()
  player.applyTimeline.mockImplementation(() => true)
  player.releaseTimelineHold.mockClear()
  player.reloadHeldForCheck.mockClear()
  player.reloadHeldForCheck.mockImplementation(() => false)
  resetTimelineExpectations()
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

describe('a play is checked again as later ranges arrive', () => {
  const AUDIO = 'https://host.example/a.mp3'
  const range = (overrides: object = {}) => ({
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
  type Body = { outcome: string; revision: number; verify_ranges_checked: number | null; sw_state: string }
  const bodies = () => reportDriftOutcome.mock.calls.map((call) => call[2] as Body)

  function play() {
    vi.stubGlobal('navigator', {
      userAgent: 'x',
      serviceWorker: { controller: {}, addEventListener: vi.fn(), register: vi.fn(async () => null) },
    })
    setCorsVerdict(AUDIO, 'cors_ok')
    beginAudioLoad(AUDIO, true)
    const hook = renderHook(() => useDriftAlignment(props()))
    return hook
  }
  const finish = () => act(() => FakeWorker.instances[0].onmessage?.({ data: { type: 'done', report } }))
  const settle = (ms: number) => act(() => vi.advanceTimersByTime(ms))

  it('finds out a copy that only differs at a later ad break', () => {
    const { result } = play()
    recordMediaReport(range()) // the opening block matches
    finish()
    settle(1500)
    expect(result.current.check?.verification?.verdict).toBe('verified')
    expect(bodies().map((b) => [b.outcome, b.revision])).toEqual([['aligned', 0]])

    // The listener seeks past the ad break: this block is not in the fetched copy.
    act(() => recordMediaReport(range({ range: 'bytes=65536-', contentLength: 2, blockIndex: 1, crc: 999, adler: 44 })))
    settle(500)
    expect(result.current.check?.verification).toMatchObject({ rangesChecked: 2, rangesMatched: 1, verdict: 'mismatch' })
    expect(bodies().map((b) => [b.outcome, b.revision])).toEqual([
      ['aligned', 0],
      ['variant_mismatch', 1],
    ])

    // A mismatch is final: nothing more is reported for this play.
    act(() => recordMediaReport(range({ blockIndex: 1, crc: 33, adler: 44 })))
    settle(500)
    expect(bodies()).toHaveLength(2)
  })

  it('reports a play that could only be verified once the worker saw it', () => {
    const { result } = play()
    finish()
    settle(1500)
    expect(result.current.check).toEqual({ swState: 'no_requests', verification: null })

    act(() => recordMediaReport(range()))
    settle(500)
    expect(result.current.check?.verification?.verdict).toBe('verified')
    expect(bodies().map((b) => [b.sw_state, b.revision])).toEqual([
      ['no_requests', 0],
      ['ok', 1],
    ])
    // More matching ranges change the count shown, not the verdict: no new report.
    act(() => recordMediaReport(range({ blockIndex: 1, crc: 33, adler: 44 })))
    settle(500)
    expect(result.current.check?.verification?.rangesChecked).toBe(2)
    expect(bodies()).toHaveLength(2)
  })

  it('checks the episode afresh when the listener comes back to it', () => {
    // Episode A, then B, then A again without a page reload: the element
    // loads A anew and may be served another copy.
    const { result, rerender } = play()
    recordMediaReport(range())
    finish()
    settle(1500)
    expect(result.current.check?.verification?.verdict).toBe('verified')

    Object.assign(player, { track: { episodeId: 'other', audioUrl: 'https://host.example/b.mp3' } })
    beginAudioLoad('https://host.example/b.mp3', true)
    rerender()
    expect(result.current.status).toBe('idle')

    settle(10)
    Object.assign(player, { track: { episodeId: 'ep-1', audioUrl: AUDIO } })
    beginAudioLoad(AUDIO, true)
    act(() => recordMediaReport(range({ crc: 999 }))) // this time, a different copy
    rerender()
    // The alignment is kept, the old verdict is not.
    expect(result.current).toMatchObject({ status: 'done', report, check: null })
    settle(1500)
    expect(result.current.check?.verification?.verdict).toBe('mismatch')
    expect(bodies().map((b) => [b.outcome, b.revision])).toEqual([
      ['aligned', 0],
      ['variant_mismatch', 0],
    ])
    // The file was not downloaded again.
    expect(FakeWorker.instances).toHaveLength(1)

    // A re-render during the same load does not check it twice.
    rerender()
    settle(1500)
    expect(bodies()).toHaveLength(2)
  })

  it('still reports the alignment when the listener moves on before it can be checked', () => {
    const { result } = play()
    recordMediaReport(range())
    finish()
    // Before the play can be checked the listener starts another episode.
    settle(100)
    beginAudioLoad('https://host.example/next.mp3', true)
    act(() => recordMediaReport(range({ url: 'https://host.example/next.mp3', crc: 999 })))
    settle(1400)

    // The outcome is recorded, with the check unknown: never judged against the new source.
    expect(reportDriftOutcome).toHaveBeenCalledTimes(1)
    const body = reportDriftOutcome.mock.calls[0][2] as Body & { verify_length_ok: boolean | null }
    expect([body.outcome, body.revision, body.sw_state, body.verify_length_ok]).toEqual(['aligned', 0, null, null])
    expect(result.current).toMatchObject({ status: 'done', report, check: null })
  })

  it('puts the map in force once the play is verified, and lifts it when a range stops matching', () => {
    const { result } = play()
    recordMediaReport(range())
    finish()
    // Nothing moves before the check.
    expect(player.applyTimeline).not.toHaveBeenCalled()
    settle(1500)

    expect(result.current.applied).toBe(true)
    const [episodeId, audioUrl, timeline] = player.applyTimeline.mock.calls[0] as [
      string,
      string,
      { map: { toAsset: (t: number) => { time: number } }; durationS: number },
    ]
    expect([episodeId, audioUrl, timeline.durationS]).toEqual(['ep-1', AUDIO, 20])
    expect(timeline.map.toAsset(5).time).toBe(13) // the report's one span plays 8 s later
    expect((bodies()[0] as Body & { applied: boolean }).applied).toBe(true)

    act(() => recordMediaReport(range({ range: 'bytes=65536-', blockIndex: 1, crc: 999, adler: 44 })))
    settle(500)
    expect(player.applyTimeline).toHaveBeenLastCalledWith('ep-1', AUDIO, null)
    expect(result.current.applied).toBe(false)
    expect(bodies().map((b) => [b.outcome, (b as Body & { applied: boolean }).applied])).toEqual([
      ['aligned', true],
      ['variant_mismatch', false],
    ])
  })

  it('applies a map that could only be verified later', () => {
    const { result } = play()
    finish()
    settle(1500)
    expect(result.current.applied).toBe(false)
    expect(player.applyTimeline).toHaveBeenLastCalledWith('ep-1', AUDIO, null)

    act(() => recordMediaReport(range()))
    settle(500)
    expect(result.current.applied).toBe(true)
    expect((bodies()[1] as Body & { applied: boolean }).applied).toBe(true)
  })

  it('never applies without a verified check, or for a copy that needs no map', () => {
    // Verified, but identical audio: nothing to correct.
    const identity = play()
    recordMediaReport(range())
    act(() =>
      FakeWorker.instances[0].onmessage?.({ data: { type: 'done', report: { ...report, outcome: 'identity' } } }),
    )
    settle(1500)
    expect(identity.result.current.check?.verification?.verdict).toBe('verified')
    expect(identity.result.current.applied).toBe(false)
    expect(player.applyTimeline.mock.calls.every((call) => call[2] === null)).toBe(true)
  })

  it('says not applied when the player refuses the map', () => {
    // The player only accepts a map for the audio it is playing right now.
    player.applyTimeline.mockImplementation(() => false)
    const { result } = play()
    recordMediaReport(range())
    finish()
    settle(1500)
    expect(result.current.applied).toBe(false)
    expect((bodies()[0] as Body & { applied: boolean }).applied).toBe(false)
  })

  it('offers a verified map again once the audio is what is playing', () => {
    // The listener was on the YouTube player when the check finished, so
    // the player refused the map; back on audio, the same load is still
    // verified and the map goes in.
    player.applyTimeline.mockImplementation(() => false)
    const { result, rerender } = play()
    recordMediaReport(range())
    finish()
    settle(1500)
    expect(result.current.check?.verification?.verdict).toBe('verified')
    expect(result.current.applied).toBe(false)

    Object.assign(player, { activeEngine: 'youtube' })
    rerender()
    expect(result.current.status).toBe('idle')

    player.applyTimeline.mockImplementation(() => true)
    Object.assign(player, { activeEngine: 'native' })
    rerender()
    expect(result.current.applied).toBe(true)
    expect(player.applyTimeline.mock.calls.at(-1)?.[2]).not.toBeNull()
    const last = bodies().at(-1) as Body & { applied: boolean }
    expect([last.outcome, last.applied, last.revision > 0]).toEqual(['aligned', true, true])
    // No second download, and no repeat on a later re-render.
    expect(FakeWorker.instances).toHaveLength(1)
    const reports = bodies().length
    rerender()
    expect(bodies()).toHaveLength(reports)

    // A further matching range updates the count shown; the map stays in
    // force and the state keeps saying so.
    act(() =>
      recordMediaReport(range({ range: 'bytes=65536-', contentLength: null, blockIndex: 1, crc: 33, adler: 44 })),
    )
    settle(500)
    expect(result.current.check?.verification).toMatchObject({ rangesChecked: 2, verdict: 'verified' })
    expect(result.current.applied).toBe(true)
    expect(player.applyTimeline.mock.calls.at(-1)?.[2]).not.toBeNull()
  })

  it('checks at once when the worker has already read the play', () => {
    const { result } = play()
    recordMediaReport(range())
    finish()
    settle(250)
    expect(result.current.check?.verification?.verdict).toBe('verified')
    expect(result.current.applied).toBe(true)
    expect(bodies()).toHaveLength(1)
    // …and does not report it a second time when the full delay has passed.
    settle(1500)
    expect(bodies()).toHaveLength(1)
  })

  it('tells the player whether a start from the transcript should wait for a map', () => {
    // Before anything has played: the episode has a track, so yes.
    Object.assign(player, { track: null, isPlaying: false })
    const waiting = renderHook((p) => useDriftAlignment(p), { initialProps: props() })
    expect(isTimelineExpected('ep-1')).toBe(true)
    waiting.rerender(props({ enabled: false }))
    expect(isTimelineExpected('ep-1')).toBe(false)
    waiting.rerender(props({ transcript: { ...transcript, sync_track_generation: null } }))
    expect(isTimelineExpected('ep-1')).toBe(false)
    waiting.unmount()

    // While aligning, and once a map is applied: yes.
    Object.assign(player, { track: { episodeId: 'ep-1', audioUrl: AUDIO }, isPlaying: true })
    const applied = play()
    expect(isTimelineExpected('ep-1')).toBe(true)
    recordMediaReport(range())
    finish()
    settle(1500)
    expect(applied.result.current.applied).toBe(true)
    expect(isTimelineExpected('ep-1')).toBe(true)
    // The reader closes: nothing here can produce a map any more.
    applied.unmount()
    expect(isTimelineExpected('ep-1')).toBe(false)
  })

  it('expects no map for an HLS stream', () => {
    Object.assign(player, {
      track: {
        episodeId: 'ep-1',
        audioUrl: 'x',
        playback: { kind: 'audio', audio: { url: 'https://h/x.m3u8', mime_type: 'application/vnd.apple.mpegurl' } },
      },
    })
    renderHook(() => useDriftAlignment(props()))
    // Otherwise a paragraph click would be ten seconds of silence for nothing.
    expect(isTimelineExpected('ep-1')).toBe(false)
  })

  it('stops expecting a map once a check has ended without one', () => {
    // Identical audio: nothing to correct, so no click should ever wait.
    const identity = play()
    recordMediaReport(range())
    act(() =>
      FakeWorker.instances[0].onmessage?.({ data: { type: 'done', report: { ...report, outcome: 'identity' } } }),
    )
    // The held start, if any, is let go the moment the outcome is known.
    expect(player.releaseTimelineHold).toHaveBeenCalledWith('ep-1', AUDIO)
    settle(1500)
    expect(isTimelineExpected('ep-1')).toBe(false)
    identity.unmount()
  })

  it('reloads a held start once so that it can be checked, then applies the map', () => {
    // First play on a host: the audio loaded without crossorigin, so the
    // play cannot be checked. The start is being held, so reload it.
    vi.stubGlobal('navigator', {
      userAgent: 'x',
      serviceWorker: { controller: {}, addEventListener: vi.fn(), register: vi.fn(async () => null) },
    })
    beginAudioLoad(AUDIO, true) // no verdict yet: a plain load
    player.reloadHeldForCheck.mockImplementation(() => {
      // What the player does: the same audio, now with crossorigin.
      beginAudioLoad(AUDIO, true)
      return true
    })
    const { result } = renderHook(() => useDriftAlignment(props()))
    finish()
    settle(1500)
    expect(player.reloadHeldForCheck).toHaveBeenCalledTimes(1)
    // Not reported, not released: the check of the reloaded audio decides.
    expect(bodies()).toHaveLength(0)
    expect(result.current.check).toBeNull()

    act(() => recordMediaReport(range()))
    settle(250)
    expect(result.current.applied).toBe(true)
    expect(bodies().map((b) => [b.outcome, b.sw_state, b.revision])).toEqual([['aligned', 'ok', 0]])
    expect(player.reloadHeldForCheck).toHaveBeenCalledTimes(1)
  })

  it('lets a held start go when even the reloaded audio cannot be checked', () => {
    vi.stubGlobal('navigator', {
      userAgent: 'x',
      serviceWorker: { controller: {}, addEventListener: vi.fn(), register: vi.fn(async () => null) },
    })
    beginAudioLoad(AUDIO, true)
    player.reloadHeldForCheck.mockImplementation(() => {
      beginAudioLoad(AUDIO, true)
      return true
    })
    const { result } = renderHook(() => useDriftAlignment(props()))
    finish()
    settle(1500)
    player.releaseTimelineHold.mockClear()
    // The worker reports nothing for the reloaded audio either.
    settle(1500)
    expect(player.reloadHeldForCheck).toHaveBeenCalledTimes(1)
    expect(player.releaseTimelineHold).toHaveBeenCalledWith('ep-1', AUDIO)
    expect(result.current.applied).toBe(false)
    expect(bodies().map((b) => [b.outcome, b.sw_state])).toEqual([['aligned', 'no_requests']])
    // No more waiting on clicks for this load.
    expect(isTimelineExpected('ep-1')).toBe(false)
  })

  it('stops when another source is loaded', () => {
    const { result } = play()
    recordMediaReport(range())
    finish()
    settle(1500)
    beginAudioLoad('https://host.example/next.mp3', true)
    act(() => recordMediaReport(range({ crc: 999 })))
    settle(500)
    expect(result.current.check?.verification?.verdict).toBe('verified')
    expect(bodies()).toHaveLength(1)
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
