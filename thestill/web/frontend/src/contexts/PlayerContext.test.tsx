// Spec #61 — unified audio/video playback session tests.
//
// jsdom implements no real media playback, so the HTMLMediaElement surface
// the session relies on (play/pause/currentTime/playbackRate/paused) is
// stubbed at the prototype level with stored state per element.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render } from '@testing-library/react'
import {
  PlayerProvider,
  usePlayer,
  type PlayerContextValue,
  type PlayerTrack,
} from './PlayerContext'
import type { PlaybackManifest } from '../api/types'

// ---------------------------------------------------------------------------
// YouTube IFrame API mock (spec #62) — shared fixture; no script tag is
// ever injected, and FakeYTPlayer records transport calls.
// ---------------------------------------------------------------------------

import { fakePlayers, resetFakePlayers } from './playback-engine/youtube-player-fake'

vi.mock('./playback-engine/youtube-iframe-api', async () => {
  const { fakeYouTubeApi } = await import('./playback-engine/youtube-player-fake')
  return { loadYouTubeIframeApi: vi.fn(() => Promise.resolve(fakeYouTubeApi)) }
})

// ---------------------------------------------------------------------------
// HTMLMediaElement stubs
// ---------------------------------------------------------------------------

interface MediaState {
  currentTime: number
  playbackRate: number
  defaultPlaybackRate: number
  paused: boolean
  volume: number
  muted: boolean
}

const mediaState = new WeakMap<HTMLMediaElement, MediaState>()

function stateFor(el: HTMLMediaElement): MediaState {
  let s = mediaState.get(el)
  if (!s) {
    s = { currentTime: 0, playbackRate: 1, defaultPlaybackRate: 1, paused: true, volume: 1, muted: false }
    mediaState.set(el, s)
  }
  return s
}

let playSpy: ReturnType<typeof vi.fn>
let pauseSpy: ReturnType<typeof vi.fn>

const originalDescriptors: Record<string, PropertyDescriptor | undefined> = {}

function installMediaStubs() {
  const proto = HTMLMediaElement.prototype as HTMLMediaElement

  playSpy = vi.fn()
  pauseSpy = vi.fn()

  for (const prop of ['currentTime', 'playbackRate', 'defaultPlaybackRate', 'paused', 'volume', 'muted']) {
    originalDescriptors[prop] = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, prop)
  }

  Object.defineProperty(proto, 'currentTime', {
    configurable: true,
    get() { return stateFor(this).currentTime },
    set(v: number) { stateFor(this).currentTime = v },
  })
  Object.defineProperty(proto, 'playbackRate', {
    configurable: true,
    get() { return stateFor(this).playbackRate },
    set(v: number) { stateFor(this).playbackRate = v },
  })
  Object.defineProperty(proto, 'defaultPlaybackRate', {
    configurable: true,
    get() { return stateFor(this).defaultPlaybackRate },
    set(v: number) { stateFor(this).defaultPlaybackRate = v },
  })
  Object.defineProperty(proto, 'paused', {
    configurable: true,
    get() { return stateFor(this).paused },
  })
  Object.defineProperty(proto, 'volume', {
    configurable: true,
    get() { return stateFor(this).volume },
    set(v: number) { stateFor(this).volume = v },
  })
  Object.defineProperty(proto, 'muted', {
    configurable: true,
    get() { return stateFor(this).muted },
    set(v: boolean) { stateFor(this).muted = v },
  })

  HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) {
    playSpy()
    stateFor(this).paused = false
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  }
  HTMLMediaElement.prototype.pause = function (this: HTMLMediaElement) {
    pauseSpy()
    stateFor(this).paused = true
    this.dispatchEvent(new Event('pause'))
  }
  HTMLMediaElement.prototype.load = function () {}
}

function restoreMediaStubs() {
  for (const [prop, desc] of Object.entries(originalDescriptors)) {
    if (desc) Object.defineProperty(HTMLMediaElement.prototype, prop, desc)
    else delete (HTMLMediaElement.prototype as unknown as Record<string, unknown>)[prop]
  }
}

// ---------------------------------------------------------------------------
// Media Session mock
// ---------------------------------------------------------------------------

function installMediaSessionMock() {
  const mock = {
    metadata: null as unknown,
    playbackState: 'none' as string,
    setActionHandler: vi.fn(),
    setPositionState: vi.fn(),
  }
  Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: mock })
  vi.stubGlobal(
    'MediaMetadata',
    class {
      title: string
      artist: string
      artwork: unknown[]
      constructor(init: { title: string; artist: string; artwork: unknown[] }) {
        this.title = init.title
        this.artist = init.artist
        this.artwork = init.artwork
      }
    },
  )
  return mock
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

// Captured in an effect (not during render, per react-hooks/globals) and
// read through a proxy so assertions always see the latest context value.
const ctxHolder: { current: PlayerContextValue | null } = { current: null }
const ctx = new Proxy({} as PlayerContextValue, {
  get: (_target, prop) => ctxHolder.current![prop as keyof PlayerContextValue],
})

function Probe() {
  const player = usePlayer()
  useEffect(() => {
    ctxHolder.current = player
  })
  return null
}

function renderPlayer() {
  const utils = render(
    <PlayerProvider>
      <Probe />
    </PlayerProvider>,
  )
  const video = document.querySelector('video') as HTMLVideoElement
  return { ...utils, video }
}

const audioTrack: PlayerTrack = {
  episodeId: 'ep-audio',
  podcastSlug: 'pod',
  episodeSlug: 'ep-1',
  title: 'Audio Episode',
  podcastTitle: 'The Pod',
  audioUrl: 'https://cdn.test/ep1.mp3',
  artworkUrl: 'https://cdn.test/art.jpg',
}

const dualManifest: PlaybackManifest = {
  kind: 'video',
  video: { url: 'https://cdn.test/ep2.mp4', mime_type: 'video/mp4', timeline_offset: 2 },
  audio: { url: 'https://cdn.test/ep2.mp3', mime_type: 'audio/mpeg', timeline_offset: 0 },
  poster_url: 'https://cdn.test/poster.jpg',
  captions_url: null,
}

const videoTrack: PlayerTrack = {
  episodeId: 'ep-video',
  podcastSlug: 'pod',
  episodeSlug: 'ep-2',
  title: 'Video Episode',
  audioUrl: 'https://cdn.test/ep2.mp4',
  playback: dualManifest,
}

let mediaSessionMock: ReturnType<typeof installMediaSessionMock>

beforeEach(() => {
  installMediaStubs()
  mediaSessionMock = installMediaSessionMock()
})

afterEach(() => {
  restoreMediaStubs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------

describe('playback session basics', () => {
  it('plays a plain audio track through the stable media element', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack))
    expect(video.src).toBe('https://cdn.test/ep1.mp3')
    expect(playSpy).toHaveBeenCalledTimes(1)
    expect(ctx.mediaKind).toBe('audio')
    expect(ctx.presentation).toBe('hidden')
    expect(ctx.activeRendition).toBe('audio')
  })

  it('resumes the same episode without reassigning the source', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack))
    video.currentTime = 100
    act(() => ctx.play(audioTrack))
    expect(video.src).toBe('https://cdn.test/ep1.mp3')
    expect(video.currentTime).toBe(100) // position untouched by resume
    expect(playSpy).toHaveBeenCalledTimes(2)
  })

  it('applies startAt on resume of the same source', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack))
    act(() => ctx.play(audioTrack, { startAt: 42 }))
    expect(video.currentTime).toBe(42)
  })

  it('defers startAt for a new track until metadata is loaded', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack, { startAt: 30 }))
    expect(video.currentTime).toBe(0)
    act(() => { video.dispatchEvent(new Event('loadedmetadata')) })
    expect(video.currentTime).toBe(30)
  })

  // startAt is ENGINE (asset-timeline) time, the same convention as seek():
  // transcript segments and summary citations already fold the per-episode
  // playback offset into the seconds they emit, so play() must assign it
  // verbatim. Re-adding the asset's timelineOffset would double-apply it.
  it('does not re-apply the asset timelineOffset to startAt on a new track', () => {
    const { video } = renderPlayer()
    // videoTrack's video asset carries timeline_offset: 2.
    act(() => ctx.play(videoTrack, { startAt: 30 }))
    act(() => { video.dispatchEvent(new Event('loadedmetadata')) })
    expect(video.currentTime).toBe(30) // not 32
  })

  it('does not re-apply the asset timelineOffset to startAt across a source transition', () => {
    const { video } = renderPlayer()
    act(() => ctx.play({ ...videoTrack, playback: null, audioUrl: 'https://cdn.test/old-ep2.mp3' }))
    video.currentTime = 50
    // Same episode replayed with the full manifest AND an explicit startAt:
    // the explicit position wins over the carried-position conversion and is
    // already on the target asset's timeline.
    act(() => ctx.play(videoTrack, { startAt: 30 }))
    expect(video.src).toBe('https://cdn.test/ep2.mp4')
    act(() => { video.dispatchEvent(new Event('loadedmetadata')) })
    expect(video.currentTime).toBe(30) // not 32
  })
})

describe('spec #61 §5 — source URL comparison on same-episode play', () => {
  it('performs a controlled source transition when the desired source differs', () => {
    const { video } = renderPlayer()
    // Started from a surface that only knew audio_url…
    act(() => ctx.play({ ...videoTrack, playback: null, audioUrl: 'https://cdn.test/old-ep2.mp3' }))
    expect(video.src).toBe('https://cdn.test/old-ep2.mp3')
    video.currentTime = 50
    video.playbackRate = 1.5

    // …then the reader plays the same episode with the full manifest.
    act(() => ctx.play(videoTrack))

    // Rendition upgraded to video; source actually switched (the old branch
    // would have silently resumed the stale source).
    expect(video.src).toBe('https://cdn.test/ep2.mp4')
    expect(ctx.activeRendition).toBe('video')
    // Position carried across the transition, offset-adjusted
    // (logical 50 → engine 50 - 0 + 2 = 52), applied at loadedmetadata.
    act(() => { video.dispatchEvent(new Event('loadedmetadata')) })
    expect(video.currentTime).toBe(52)
    // Rate carried across the load boundary.
    expect(video.playbackRate).toBe(1.5)
    expect(video.defaultPlaybackRate).toBe(1.5)
  })
})

describe('spec #61 §5 — rendition switching', () => {
  it('switches video → audio preserving logical position, rate and play state', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(videoTrack))
    expect(video.src).toBe('https://cdn.test/ep2.mp4')
    expect(ctx.activeRendition).toBe('video')
    video.currentTime = 10
    video.playbackRate = 2

    act(() => ctx.switchRendition('audio'))

    expect(video.src).toBe('https://cdn.test/ep2.mp3')
    expect(ctx.activeRendition).toBe('audio')
    act(() => { video.dispatchEvent(new Event('loadedmetadata')) })
    // Engine 10 on the video timeline (offset 2) = logical 8 = engine 8 on
    // the audio timeline (offset 0).
    expect(video.currentTime).toBe(8)
    expect(video.playbackRate).toBe(2)
    // Was playing → still playing after the transition.
    expect(playSpy).toHaveBeenCalledTimes(2)
  })

  it('ignores a switch to an unavailable rendition', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack))
    act(() => ctx.switchRendition('video'))
    expect(video.src).toBe('https://cdn.test/ep1.mp3')
    expect(ctx.activeRendition).toBe('audio')
  })

  it('exposes canSwitchRendition only when both assets exist', () => {
    renderPlayer()
    act(() => ctx.play(audioTrack))
    expect(ctx.canSwitchRendition).toBe(false)
    act(() => ctx.play(videoTrack))
    expect(ctx.canSwitchRendition).toBe(true)
  })
})

describe('spec #61 §3 — presentation state machine', () => {
  it('is hidden for audio tracks regardless of surfaces', () => {
    renderPlayer()
    act(() => ctx.play(audioTrack))
    const slot = document.createElement('div')
    act(() => { ctx.registerTheaterSlot('ep-audio', slot) })
    expect(ctx.presentation).toBe('hidden')
  })

  it('floats video when no theater slot is registered, theater wins when one is', () => {
    renderPlayer()
    act(() => ctx.play(videoTrack))
    expect(ctx.presentation).toBe('floating')

    const slot = document.createElement('div')
    let unregister!: () => void
    act(() => { unregister = ctx.registerTheaterSlot('ep-video', slot) })
    expect(ctx.presentation).toBe('theater')

    act(() => unregister())
    expect(ctx.presentation).toBe('floating')
  })

  it("ignores a theater slot registered for a different episode", () => {
    renderPlayer()
    act(() => ctx.play(videoTrack))
    const slot = document.createElement('div')
    act(() => { ctx.registerTheaterSlot('some-other-episode', slot) })
    expect(ctx.presentation).toBe('floating')
  })

  it('videoPreference audio-only hides the floating surface', () => {
    renderPlayer()
    act(() => ctx.play(videoTrack))
    act(() => ctx.setVideoPreference('audio-only'))
    expect(ctx.presentation).toBe('hidden')
    act(() => ctx.setVideoPreference('shown'))
    expect(ctx.presentation).toBe('floating')
  })

  it('switching to the audio rendition drops presentation to hidden', () => {
    renderPlayer()
    act(() => ctx.play(videoTrack))
    expect(ctx.presentation).toBe('floating')
    act(() => ctx.switchRendition('audio'))
    expect(ctx.presentation).toBe('hidden')
  })
})

describe('spec #61 — continuity regression: surface changes never reset playback', () => {
  it('registering and unregistering surfaces does not touch the engine', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(videoTrack))
    video.currentTime = 123
    const srcBefore = video.src
    playSpy.mockClear()
    pauseSpy.mockClear()

    const theater = document.createElement('div')
    const floating = document.createElement('div')
    let unTheater!: () => void
    let unFloating!: () => void
    act(() => { unFloating = ctx.registerFloatingSlot(floating) })
    act(() => { unTheater = ctx.registerTheaterSlot('ep-video', theater) })
    act(() => unTheater())
    act(() => unFloating())
    act(() => ctx.setVideoPreference('audio-only'))
    act(() => ctx.setVideoPreference('shown'))

    // Transport state is entirely unaffected by presentation churn.
    expect(video.src).toBe(srcBefore)
    expect(video.currentTime).toBe(123)
    expect(video.paused).toBe(false)
    expect(playSpy).not.toHaveBeenCalled()
    expect(pauseSpy).not.toHaveBeenCalled()
  })

  it('keeps the media element mounted and playing across presentation changes', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(videoTrack))
    const slot = document.createElement('div')
    act(() => { ctx.registerTheaterSlot('ep-video', slot) })
    // Same DOM node — never reparented, never remounted.
    expect(document.querySelector('video')).toBe(video)
    expect(video.paused).toBe(false)
  })
})

describe('spec #61 §2 — Media Session integration', () => {
  it('publishes track metadata', () => {
    renderPlayer()
    act(() => ctx.play(audioTrack))
    const metadata = mediaSessionMock.metadata as { title: string; artist: string; artwork: Array<{ src: string }> }
    expect(metadata.title).toBe('Audio Episode')
    expect(metadata.artist).toBe('The Pod')
    expect(metadata.artwork[0].src).toBe('https://cdn.test/art.jpg')
  })

  it('mirrors playback state', () => {
    const { video } = renderPlayer()
    expect(mediaSessionMock.playbackState).toBe('none')
    act(() => ctx.play(audioTrack))
    expect(mediaSessionMock.playbackState).toBe('playing')
    act(() => { video.pause() })
    expect(mediaSessionMock.playbackState).toBe('paused')
    act(() => ctx.stop())
    expect(mediaSessionMock.playbackState).toBe('none')
  })

  it('registers feature-detected action handlers', () => {
    renderPlayer()
    const actions = mediaSessionMock.setActionHandler.mock.calls.map((c) => c[0])
    for (const expected of ['play', 'pause', 'stop', 'seekbackward', 'seekforward', 'seekto']) {
      expect(actions).toContain(expected)
    }
  })

  it('seekbackward/seekforward handlers drive the session transport', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(audioTrack))
    video.currentTime = 100
    const handlerFor = (action: string) =>
      mediaSessionMock.setActionHandler.mock.calls.filter((c) => c[0] === action && c[1] !== null).at(-1)?.[1]
    act(() => handlerFor('seekbackward')({}))
    expect(video.currentTime).toBe(85)
    act(() => handlerFor('seekto')({ seekTime: 7 }))
    expect(video.currentTime).toBe(7)
  })
})

describe('stop', () => {
  it('clears the session and rendition state', () => {
    const { video } = renderPlayer()
    act(() => ctx.play(videoTrack))
    act(() => ctx.stop())
    expect(ctx.track).toBeNull()
    expect(ctx.presentation).toBe('hidden')
    expect(ctx.activeRendition).toBe('audio')
    expect(video.hasAttribute('src')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Spec #62 — YouTube iframe engine
// ---------------------------------------------------------------------------

const youtubeTrack: PlayerTrack = {
  episodeId: 'ep-yt',
  podcastSlug: 'pod',
  episodeSlug: 'ep-3',
  title: 'YouTube Episode',
  podcastTitle: 'The Pod',
  audioUrl: 'https://cdn.test/ep3.mp3',
  playback: {
    kind: 'audio',
    audio: { url: 'https://cdn.test/ep3.mp3', mime_type: 'audio/mpeg', timeline_offset: 0 },
    video: null,
    youtube: { video_id: 'validVID001', watch_url: 'https://www.youtube.com/watch?v=validVID001' },
    poster_url: 'https://cdn.test/poster3.jpg',
    captions_url: null,
  },
}

// Enters the YouTube rendition the way the reader does: the theater slot
// registers in the same commit as the engine flip (in the app the theater
// mounts off `activeEngine`; here both run inside one act()).
async function enterYouTube(slot: HTMLElement) {
  await act(async () => {
    ctx.playYouTube(youtubeTrack)
    ctx.registerTheaterSlot('ep-yt', slot)
  })
  return fakePlayers.at(-1)!
}

describe('spec #62 — YouTube iframe engine', () => {
  beforeEach(() => {
    resetFakePlayers()
  })

  it('exposes youtubeAvailable from the manifest', () => {
    renderPlayer()
    act(() => ctx.play(audioTrack))
    expect(ctx.youtubeAvailable).toBe(false)
    act(() => ctx.play(youtubeTrack))
    expect(ctx.youtubeAvailable).toBe(true)
  })

  it('playYouTube enters the engine, carries logical position, and swaps node visibility', async () => {
    const { video } = renderPlayer()
    act(() => ctx.play(youtubeTrack))
    video.currentTime = 30

    const player = await enterYouTube(document.createElement('div'))

    expect(ctx.activeEngine).toBe('youtube')
    expect(ctx.presentation).toBe('theater')
    expect(video.paused).toBe(true) // native paused, source retained
    expect(video.src).toBe('https://cdn.test/ep3.mp3')
    expect(player.loadVideoById).toHaveBeenCalledWith('validVID001', 30)
    expect(player.playVideo).toHaveBeenCalled()
    // One visible node at a time.
    expect(video.style.display).toBe('none')
    expect((document.querySelector('[data-testid="player-youtube-layer"]') as HTMLElement).style.display).not.toBe('none')
  })

  it('switchRendition(audio) leaves YouTube carrying the iframe clock best-effort', async () => {
    const { video } = renderPlayer()
    act(() => ctx.play(youtubeTrack))
    const player = await enterYouTube(document.createElement('div'))
    player.time = 100
    act(() => player.emit(1)) // PLAYING

    act(() => ctx.switchRendition('audio'))

    expect(ctx.activeEngine).toBe('native')
    expect(ctx.activeRendition).toBe('audio')
    // Same source URL as before the YouTube detour → in-place seek. The
    // interpolated clock may add sub-millisecond wall time — best-effort
    // is the contract (§8).
    expect(video.src).toBe('https://cdn.test/ep3.mp3')
    expect(video.currentTime).toBeCloseTo(100, 1)
    expect(video.paused).toBe(false) // was playing → still playing
    expect(player.pauseVideo).toHaveBeenCalled()
  })

  it('§7: hiding video while on YouTube switches to the audio rendition', async () => {
    renderPlayer()
    act(() => ctx.play(youtubeTrack))
    await enterYouTube(document.createElement('div'))
    expect(ctx.activeEngine).toBe('youtube')

    act(() => ctx.setVideoPreference('audio-only'))

    expect(ctx.activeEngine).toBe('native')
    expect(ctx.activeRendition).toBe('audio')
    expect(ctx.presentation).toBe('hidden')
  })

  it('§7: unregistering the last presenting surface switches to audio', async () => {
    renderPlayer()
    act(() => ctx.play(youtubeTrack))
    const slot = document.createElement('div')
    let unregister!: () => void
    await act(async () => {
      ctx.playYouTube(youtubeTrack)
      unregister = ctx.registerTheaterSlot('ep-yt', slot)
    })
    expect(ctx.activeEngine).toBe('youtube')

    act(() => unregister())

    expect(ctx.activeEngine).toBe('native')
    expect(ctx.activeRendition).toBe('audio')
  })

  it('a new episode always starts on the native engine', async () => {
    const { video } = renderPlayer()
    act(() => ctx.play(youtubeTrack))
    const player = await enterYouTube(document.createElement('div'))

    act(() => ctx.play(audioTrack))

    expect(ctx.activeEngine).toBe('native')
    expect(video.src).toBe('https://cdn.test/ep1.mp3')
    expect(player.pauseVideo).toHaveBeenCalled()
  })

  it('stop destroys the iframe player and resets the engine', async () => {
    renderPlayer()
    act(() => ctx.play(youtubeTrack))
    const player = await enterYouTube(document.createElement('div'))

    act(() => ctx.stop())

    expect(player.destroy).toHaveBeenCalled()
    expect(ctx.activeEngine).toBe('native')
    expect(ctx.track).toBeNull()
  })

  it('hides the PiP affordance while the YouTube engine is active', async () => {
    renderPlayer()
    act(() => ctx.play(youtubeTrack))
    await enterYouTube(document.createElement('div'))
    expect(ctx.pipSupported).toBe(false)
  })

  it('playYouTube clears a prior "Hide video" so the opt-in is never a no-op', async () => {
    // Review regression: videoPreference is session-global; without the
    // reset, the §7 effect saw an unpresented iframe and bounced straight
    // back to audio, making "Watch video" a visible no-op.
    renderPlayer()
    act(() => ctx.play(youtubeTrack))
    act(() => ctx.setVideoPreference('audio-only'))

    await enterYouTube(document.createElement('div'))

    expect(ctx.videoPreference).toBe('shown')
    expect(ctx.activeEngine).toBe('youtube')
    expect(ctx.presentation).toBe('theater')
  })

  it('playYouTube exits native picture-in-picture before switching engines', async () => {
    const { video } = renderPlayer()
    const exitSpy = vi.fn(() => Promise.resolve())
    Object.defineProperty(document, 'pictureInPictureElement', { configurable: true, value: video })
    Object.defineProperty(document, 'exitPictureInPicture', { configurable: true, value: exitSpy })
    try {
      act(() => ctx.play(youtubeTrack))
      await enterYouTube(document.createElement('div'))
      expect(exitSpy).toHaveBeenCalled()
    } finally {
      delete (document as unknown as Record<string, unknown>).pictureInPictureElement
      delete (document as unknown as Record<string, unknown>).exitPictureInPicture
    }
  })
})
