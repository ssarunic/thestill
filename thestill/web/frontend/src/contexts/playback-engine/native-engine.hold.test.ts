// Spec #116 — a start from a transcript time held, silent, for its timeline
// map: the listener must never hear the uncorrected position first.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { NativeEngine, TIMELINE_HOLD_MAX_MS, type AppliedTimeline } from './native-engine'
import { TimelineMap } from './timeline-map'

const URL_A = 'https://cdn.test/a.mp3'

// Transcript time 26–46 s plays 9 s later in this copy.
const timeline: AppliedTimeline = {
  map: new TimelineMap(
    [
      { srcStartS: 0, srcEndS: 20, offsetS: 5 },
      { srcStartS: 26, srcEndS: 46, offsetS: 9 },
    ],
    [{ srcStartS: 20, srcEndS: 26, kind: 'confirmed' }],
  ),
  durationS: 46,
}

function makeEngine() {
  const el = document.createElement('video')
  let readyState = 0
  let currentTime = 0
  Object.defineProperty(el, 'readyState', { configurable: true, get: () => readyState })
  Object.defineProperty(el, 'duration', { configurable: true, get: () => 55 })
  Object.defineProperty(el, 'currentTime', {
    configurable: true,
    get: () => currentTime,
    set: (v: number) => {
      currentTime = v
    },
  })
  el.play = vi.fn(async () => {})
  el.pause = vi.fn()
  const onTimelineHoldChange = vi.fn()
  const onVolumeChange = vi.fn()
  const noop = vi.fn()
  const events = new Proxy({} as EngineEvents, {
    get: (_t, name) =>
      name === 'onTimelineHoldChange' ? onTimelineHoldChange : name === 'onVolumeChange' ? onVolumeChange : noop,
  })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    onTimelineHoldChange,
    onVolumeChange,
    metadataLoaded: () => {
      readyState = HTMLMediaElement.HAVE_METADATA
      el.dispatchEvent(new Event('loadedmetadata'))
    },
    elementTime: () => currentTime,
    playTo: (seconds: number) => {
      currentTime = seconds
    },
  }
}

/** A click on the paragraph at 30 s of the transcript, as the first action. */
function clickedStart() {
  const t = makeEngine()
  t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
  void t.engine.play()
  t.metadataLoaded()
  return t
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('NativeEngine holding a transcript-time start', () => {
  it('plays muted until the map arrives, then starts on the clicked words', () => {
    const t = clickedStart()
    expect(t.engine.isHoldingForTimeline()).toBe(true)
    expect(t.onTimelineHoldChange).toHaveBeenLastCalledWith(true)
    expect(t.el.muted).toBe(true)
    expect(t.elementTime()).toBe(30) // the uncorrected position, unheard
    t.playTo(33.5) // it keeps playing, silently, while the map is prepared

    expect(t.engine.setTimeline(URL_A, timeline)).toBe(true)

    expect(t.elementTime()).toBe(39) // where the 30 s paragraph is in this copy
    expect(t.el.muted).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.onTimelineHoldChange).toHaveBeenLastCalledWith(false)
    expect(t.engine.getCurrentTime()).toBe(30)
  })

  it('does not tell the app about its own muting, and gives back the listener’s setting', () => {
    const t = makeEngine()
    t.el.muted = true // the listener had muted the player
    t.engine.load({ url: URL_A }, { seekTo: 30, holdForTimeline: true })
    t.metadataLoaded()
    t.el.dispatchEvent(new Event('volumechange'))
    expect(t.onVolumeChange).not.toHaveBeenCalled()
    t.engine.setTimeline(URL_A, timeline)
    expect(t.el.muted).toBe(true)

    // Unmuting during a hold takes effect when the hold ends.
    const other = clickedStart()
    other.engine.setMuted(true)
    expect(other.onVolumeChange).toHaveBeenLastCalledWith(other.el.volume, true)
    other.engine.setTimeline(URL_A, timeline)
    expect(other.el.muted).toBe(true)
  })

  it('starts at the asked-for time, uncorrected, when told no map is coming', () => {
    const t = clickedStart()
    t.playTo(34)
    t.engine.releaseTimelineHold(URL_A)
    expect(t.elementTime()).toBe(30) // from the top of what was clicked
    expect(t.el.muted).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    // A map that turns up afterwards does not yank the audio.
    t.playTo(41)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(41)
  })

  it('gives up waiting after the cap', () => {
    const t = clickedStart()
    t.playTo(38)
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS - 1)
    expect(t.el.muted).toBe(true)
    vi.advanceTimersByTime(1)
    expect(t.el.muted).toBe(false)
    expect(t.elementTime()).toBe(30)
  })

  it('holds a click made while already playing, before the map is in force', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    void t.engine.play()
    t.metadataLoaded()
    t.playTo(4)
    t.engine.seekTo(30, { holdForTimeline: true })
    expect(t.el.muted).toBe(true)
    // A second click replaces the first.
    t.engine.seekTo(10, { holdForTimeline: true })
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(15)
    expect(t.el.muted).toBe(false)
  })

  it('does not hold once a map is in force: the seek is simply correct', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    t.metadataLoaded()
    t.engine.setTimeline(URL_A, timeline)
    t.engine.seekTo(30, { holdForTimeline: true })
    expect(t.el.muted).toBe(false)
    expect(t.elementTime()).toBe(39)
  })

  it('ends the hold when the listener picks a place themselves', () => {
    const t = clickedStart()
    t.engine.seekTo(12) // the scrubber
    expect(t.el.muted).toBe(false)
    expect(t.elementTime()).toBe(12)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(12)
  })

  it('unmutes on pause but still honours the click when the map arrives', () => {
    const t = clickedStart()
    t.engine.pause()
    expect(t.el.muted).toBe(false)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
  })

  it('keeps holding across the reload that makes the audio checkable', () => {
    // First play on a host: loaded without crossorigin, then reloaded with it.
    const t = makeEngine()
    t.engine.load({ url: URL_A }, { seekTo: 30, holdForTimeline: true })
    void t.engine.play()
    t.metadataLoaded()
    t.onTimelineHoldChange.mockClear()

    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    expect(t.el.getAttribute('crossorigin')).toBe('anonymous')
    expect(t.el.muted).toBe(true)
    expect(t.onTimelineHoldChange).not.toHaveBeenCalled() // one hold, not two
    expect(t.engine.heldTranscriptTime()).toBe(30)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
    expect(t.el.muted).toBe(false)
  })

  it('drops the hold with the source', () => {
    const t = clickedStart()
    t.engine.load({ url: 'https://cdn.test/b.mp3' })
    expect(t.el.muted).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)

    const u = clickedStart()
    u.engine.unload()
    expect(u.el.muted).toBe(false)
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS)
    expect(u.elementTime()).toBe(30) // the cap no longer acts on an unloaded source
  })

  it('queues the corrected position when the map arrives before metadata', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    t.engine.setTimeline(URL_A, timeline)
    t.metadataLoaded()
    expect(t.elementTime()).toBe(39)
  })
})
