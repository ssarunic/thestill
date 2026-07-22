// Spec #62 §5/§8 — YouTubeEngine against a mocked IFrame Player API:
// transport proxying, state mapping, the polled+interpolated clock, and
// error surfacing. No React involved — the engine is a plain class.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EngineEvents } from './types'
import { YouTubeEngine } from './youtube-engine'
import { fakePlayers, resetFakePlayers } from './youtube-player-fake'

vi.mock('./youtube-iframe-api', async () => {
  const { fakeYouTubeApi } = await import('./youtube-player-fake')
  return { loadYouTubeIframeApi: vi.fn(() => Promise.resolve(fakeYouTubeApi)) }
})

function makeEvents(): EngineEvents {
  return {
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onPlaying: vi.fn(),
    onWaiting: vi.fn(),
    onCanPlay: vi.fn(),
    onTimeUpdate: vi.fn(),
    onDurationChange: vi.fn(),
    onRateChange: vi.fn(),
    onSeeked: vi.fn(),
    onVolumeChange: vi.fn(),
    onError: vi.fn(),
    onEnded: vi.fn(),
  }
}

const flush = async () => {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

describe('YouTubeEngine', () => {
  let container: HTMLDivElement
  let events: EngineEvents
  let engine: YouTubeEngine

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] })
    resetFakePlayers()
    container = document.createElement('div')
    document.body.appendChild(container)
    events = makeEvents()
    engine = new YouTubeEngine(container, events)
  })

  afterEach(() => {
    engine.destroy()
    container.remove()
    vi.useRealTimers()
  })

  it('constructs the player on an inner target and applies the pending load on ready', async () => {
    engine.load({ videoId: 'validVID001' }, { seekTo: 30, autoplay: true })
    await flush()

    const player = fakePlayers[0]
    expect(player).toBeDefined()
    // The stable host is never replaced — the player targets an inner div.
    expect(player.el.parentElement === container || player.el === container.firstElementChild).toBe(true)
    expect(player.loadVideoById).toHaveBeenCalledWith('validVID001', 30)
    expect(player.playVideo).toHaveBeenCalled()
    expect(events.onDurationChange).toHaveBeenCalledWith(3600)
    expect(events.onPlaying).toHaveBeenCalled()
  })

  it('proxies transport calls to the IFrame API', async () => {
    engine.load({ videoId: 'validVID001' }, { autoplay: false })
    await flush()
    const player = fakePlayers[0]

    void engine.play()
    expect(player.playVideo).toHaveBeenCalled()
    engine.pause()
    expect(player.pauseVideo).toHaveBeenCalled()
    engine.seekTo(120)
    expect(player.seekTo).toHaveBeenCalledWith(120, true)
    engine.setRate(1.5)
    expect(player.setPlaybackRate).toHaveBeenCalledWith(1.5)
    engine.setVolume(0.5)
    expect(player.setVolume).toHaveBeenCalledWith(50)
    engine.setMuted(true)
    expect(player.mute).toHaveBeenCalled()
  })

  it('maps state changes onto EngineEvents', async () => {
    engine.load({ videoId: 'validVID001' }, { autoplay: false })
    await flush()
    const player = fakePlayers[0]

    player.emit(3) // BUFFERING
    expect(events.onWaiting).toHaveBeenCalled()
    player.emit(1) // PLAYING
    expect(events.onPlay).toHaveBeenCalled()
    player.emit(2) // PAUSED
    expect(events.onPause).toHaveBeenCalled()
    player.emit(0) // ENDED
    expect(events.onEnded).toHaveBeenCalled()
  })

  it('interpolates the polled clock while playing and freezes it when paused', async () => {
    engine.load({ videoId: 'validVID001' }, { seekTo: 10, autoplay: true })
    await flush()
    const player = fakePlayers[0]
    expect(engine.isPlaying()).toBe(true)

    // Between poll samples the clock extrapolates by wall time × rate.
    vi.advanceTimersByTime(100)
    expect(engine.getCurrentTime()).toBeCloseTo(10.1, 3)

    // A poll tick re-samples from the player and reports time updates.
    player.time = 10.25
    vi.advanceTimersByTime(150)
    expect(events.onTimeUpdate).toHaveBeenCalledWith(10.25)
    expect(engine.getCurrentTime()).toBeCloseTo(10.25, 3)

    // Paused (or an ad buffering) freezes the clock instead of drifting.
    player.emit(2)
    vi.advanceTimersByTime(2000)
    expect(engine.getCurrentTime()).toBeCloseTo(10.25, 2)
  })

  it('resets the interpolation baseline immediately on seek', async () => {
    engine.load({ videoId: 'validVID001' }, { seekTo: 10, autoplay: true })
    await flush()

    engine.seekTo(500)
    expect(engine.getCurrentTime()).toBeCloseTo(500, 1)
    expect(events.onSeeked).toHaveBeenCalled()
  })

  it('surfaces player errors through onError', async () => {
    engine.load({ videoId: 'validVID001' }, { autoplay: false })
    await flush()
    const player = fakePlayers[0]

    player.events.onError?.({ data: 150, target: player })
    expect(events.onError).toHaveBeenCalledWith('This video cannot be embedded outside YouTube.')
  })

  it('reuses the same player across episode changes and destroys cleanly', async () => {
    engine.load({ videoId: 'validVID001' }, { autoplay: false })
    await flush()
    engine.load({ videoId: 'secndVID002' }, { seekTo: 5, autoplay: false })
    await flush()

    expect(fakePlayers).toHaveLength(1)
    expect(fakePlayers[0].loadVideoById).toHaveBeenCalledWith('secndVID002', 5)

    engine.destroy()
    expect(fakePlayers[0].destroy).toHaveBeenCalled()
    expect(container.childElementCount).toBe(0)
  })
})
