// What the listener is told after pressing Play: the button shows it is
// waiting from the moment of the press, a failure is said out loud, and a
// source that never arrives does not leave the button spinning for ever.
// Also: an http enclosure is played through our https redirect.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render } from '@testing-library/react'
import { LOAD_PATIENCE_MS, PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from './PlayerContext'
import { AUDIO_NOT_LOADED_MESSAGE } from './playback-engine/native-engine'
import { TimelineMap } from './playback-engine/timeline-map'

vi.mock('../api/listening', () => ({
  putListening: vi.fn().mockResolvedValue({ kind: 'ok', ack: { accepted: true, completed: false, listening: null } }),
  getListening: vi.fn().mockResolvedValue(null),
  getLatestListening: vi.fn().mockResolvedValue(null),
  listInProgress: vi.fn(),
}))

// As in production for a Safari listener: the reader is served over https
// (jsdom's page is http, and its user agent is its own).
const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15'
vi.mock('../utils/elementAudioUrl', async (original) => {
  const actual = await original<typeof import('../utils/elementAudioUrl')>()
  return {
    elementAudioUrl: (url: string, owner: Parameters<typeof actual.elementAudioUrl>[1]) =>
      actual.elementAudioUrl(url, owner, 'https:', SAFARI),
  }
})

const ctxHolder: { current: PlayerContextValue | null } = { current: null }
const ctx = new Proxy({} as PlayerContextValue, {
  get: (_t, prop) => ctxHolder.current![prop as keyof PlayerContextValue],
})
function Probe() {
  const player = usePlayer()
  useEffect(() => {
    ctxHolder.current = player
  })
  return null
}

const track: PlayerTrack = {
  episodeId: 'ep-1',
  podcastSlug: 'pod',
  episodeSlug: 'ep-1-slug',
  title: 'Episode',
  audioUrl: 'https://cdn.test/a.mp3',
}
const bbcTrack: PlayerTrack = { ...track, episodeId: 'ep-2', episodeSlug: 'ep-2-slug', audioUrl: 'http://open.example.org/redir/a.mp3' }

let playResult: () => Promise<void>
let playAccepted = true
let readyState = 0

function renderPlayer() {
  render(
    <PlayerProvider>
      <Probe />
    </PlayerProvider>,
  )
  return document.querySelector('video') as HTMLVideoElement
}
const fire = (video: HTMLVideoElement, name: string) =>
  act(() => {
    video.dispatchEvent(new Event(name))
  })

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  readyState = 0
  playResult = () => Promise.resolve()
  playAccepted = true
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    // As a browser does: `play` fires as soon as the start is accepted,
    // long before any audio; a refused start fires nothing.
    if (playAccepted) this.dispatchEvent(new Event('play'))
    return playResult()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('pause'))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockImplementation(() => readyState)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('feedback after a press on Play', () => {
  it('is loading from the press until audio is playing', async () => {
    const video = renderPlayer()
    expect(ctx.isLoading).toBe(false)
    await act(async () => ctx.play(track))
    expect(ctx.isLoading).toBe(true)
    // The browser has accepted the play; no audio yet.
    expect(ctx.isPlaying).toBe(true)
    fire(video, 'playing')
    expect(ctx.isLoading).toBe(false)
    expect(ctx.isPlaying).toBe(true)
  })

  it('stops waiting when the listener pauses before any audio arrived', async () => {
    renderPlayer()
    await act(async () => ctx.play(track))
    act(() => ctx.pause())
    expect(ctx.isLoading).toBe(false)
    expect(ctx.isPlaying).toBe(false)
    // …and Play starts the wait again.
    await act(async () => ctx.resume())
    expect(ctx.isLoading).toBe(true)
  })

  it('stops waiting when the browser refuses to start', async () => {
    playResult = () => Promise.reject(new DOMException('no gesture', 'NotAllowedError'))
    playAccepted = false
    renderPlayer()
    await act(async () => ctx.play(track))
    expect(ctx.isLoading).toBe(false)
    expect(ctx.isPlaying).toBe(false)
  })

  it('says so when the source fails, and the button goes back to Play', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    fire(video, 'error')
    expect(ctx.mediaError).toBe(AUDIO_NOT_LOADED_MESSAGE)
    expect(ctx.isLoading).toBe(false)
    expect(ctx.isPlaying).toBe(false)
  })

  it('gives up on a source that sends nothing, with no error event at all', async () => {
    vi.useFakeTimers()
    renderPlayer()
    await act(async () => ctx.play(track))
    act(() => vi.advanceTimersByTime(LOAD_PATIENCE_MS - 1))
    expect(ctx.isLoading).toBe(true)
    expect(ctx.mediaError).toBeNull()
    act(() => vi.advanceTimersByTime(1))
    expect(ctx.mediaError).toBe(AUDIO_NOT_LOADED_MESSAGE)
    expect(ctx.isLoading).toBe(false)
    expect(ctx.isPlaying).toBe(false)
  })

  it('cancels the pending start when it gives up, so the next press plays', async () => {
    vi.useFakeTimers()
    const pause = vi.mocked(HTMLMediaElement.prototype.pause)
    const play = vi.mocked(HTMLMediaElement.prototype.play)
    renderPlayer()
    await act(async () => ctx.play(track))
    const pausesBefore = pause.mock.calls.length
    act(() => vi.advanceTimersByTime(LOAD_PATIENCE_MS))
    expect(ctx.mediaError).toBe(AUDIO_NOT_LOADED_MESSAGE)
    // Not left pending: the audio must not start later by itself.
    expect(pause.mock.calls.length).toBe(pausesBefore + 1)
    // The button says Play, and pressing it plays.
    const playsBefore = play.mock.calls.length
    await act(async () => ctx.toggle())
    expect(play.mock.calls.length).toBe(playsBefore + 1)
    expect(ctx.isLoading).toBe(true)
  })

  it('ignores the rejection of a start that another episode has replaced', async () => {
    let rejectFirst: (reason: unknown) => void = () => {}
    playResult = () =>
      new Promise<void>((_resolve, reject) => {
        rejectFirst = reject
      })
    renderPlayer()
    await act(async () => ctx.play(track))
    const rejectEpisodeOne = rejectFirst
    // The listener switches episode while the first start is still pending.
    playResult = () => new Promise<void>(() => {})
    await act(async () => ctx.play(bbcTrack))
    expect(ctx.isLoading).toBe(true)
    // Assigning the new source aborts the first start.
    await act(async () => rejectEpisodeOne(new DOMException('aborted', 'AbortError')))
    expect(ctx.isLoading).toBe(true)
  })

  it('loads the source afresh when Play is pressed again after a failure', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    Object.defineProperty(video, 'error', { configurable: true, get: () => ({ code: 4 }) })
    fire(video, 'error')
    expect(ctx.mediaError).toBe(AUDIO_NOT_LOADED_MESSAGE)

    // A failed element refuses play() without asking the network again;
    // the source has to be assigned once more.
    const assigned: string[] = []
    const src = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src')!
    Object.defineProperty(video, 'src', {
      configurable: true,
      get: () => src.get!.call(video),
      set: (value: string) => {
        assigned.push(value)
        src.set!.call(video, value)
      },
    })
    await act(async () => ctx.toggle())
    expect(assigned).toEqual(['https://cdn.test/a.mp3'])
    // The retry is the news now, not the earlier failure.
    expect(ctx.mediaError).toBeNull()
    expect(ctx.isLoading).toBe(true)
  })

  it('says nothing when a source that was only preloaded fails, and loads it on Play', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    act(() => ctx.pause())
    // The same state as a session restored on page load: loaded, not asked to play.
    fire(video, 'error')
    expect(ctx.mediaError).toBeNull()
  })

  it('stops showing the wait when the engine reports the start did not happen', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    expect(ctx.isLoading).toBe(true)
    // What a start refused after a reload, or after a held start, ends in.
    fire(video, 'pause')
    expect(ctx.isPlaying).toBe(false)
    expect(ctx.isLoading).toBe(false)
  })

  it('keeps waiting on a slow source that has started to arrive', async () => {
    vi.useFakeTimers()
    renderPlayer()
    await act(async () => ctx.play(track))
    readyState = 1 // metadata is in
    act(() => vi.advanceTimersByTime(LOAD_PATIENCE_MS + 1000))
    expect(ctx.mediaError).toBeNull()
    expect(ctx.isLoading).toBe(true)
  })

  it('does not report a source that started playing within the wait', async () => {
    vi.useFakeTimers()
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    fire(video, 'playing')
    act(() => vi.advanceTimersByTime(LOAD_PATIENCE_MS + 1000))
    expect(ctx.mediaError).toBeNull()
  })
})

describe('an http enclosure on the https reader, in Safari', () => {
  it('is loaded through our redirect, while the source keeps its own address', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(bbcTrack))
    expect(video.getAttribute('src')).toBe('/api/podcasts/pod/episodes/ep-2-slug/audio')
    // A map is still offered for the enclosure's own address.
    let accepted = false
    act(() => {
      accepted = ctx.applyTimeline('ep-2', bbcTrack.audioUrl, { map: TimelineMap.constant(0), durationS: 60 })
    })
    expect(accepted).toBe(true)
  })

  it('loads an https enclosure directly', async () => {
    const video = renderPlayer()
    await act(async () => ctx.play(track))
    expect(video.getAttribute('src')).toBe('https://cdn.test/a.mp3')
  })
})
