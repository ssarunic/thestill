// Spec #116 — a start from a transcript time held, paused, for its timeline
// map: the listener must never hear the uncorrected position first. Paused
// and not muted: Safari ends a muted audio-only playback at once.
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
  // A stand-in for the element's own play state, events included.
  let paused = true
  let refusePlay = false
  Object.defineProperty(el, 'paused', { configurable: true, get: () => paused })
  const play = vi.fn(async () => {
    if (refusePlay) throw new DOMException('no gesture', 'NotAllowedError')
    if (paused) {
      paused = false
      el.dispatchEvent(new Event('play'))
    }
  })
  el.play = play
  el.pause = vi.fn(() => {
    if (!paused) {
      paused = true
      el.dispatchEvent(new Event('pause'))
    }
  })
  const onTimelineHoldChange = vi.fn()
  const onPlay = vi.fn()
  const onPause = vi.fn()
  const noop = vi.fn()
  const named: Record<string, unknown> = { onTimelineHoldChange, onPlay, onPause }
  const events = new Proxy({} as EngineEvents, { get: (_t, name) => named[name as string] ?? noop })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    play,
    onTimelineHoldChange,
    onPlay,
    onPause,
    refusePlay: () => {
      refusePlay = true
    },
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
  it('waits paused until the map arrives, then starts on the clicked words', () => {
    const t = clickedStart()
    expect(t.engine.isHoldingForTimeline()).toBe(true)
    expect(t.onTimelineHoldChange).toHaveBeenLastCalledWith(true)
    // Nothing is heard, and nothing is muted to get there.
    expect(t.el.paused).toBe(true)
    expect(t.el.muted).toBe(false)
    // For the listener playback is under way.
    expect(t.engine.isPlaying()).toBe(true)
    expect(t.onPlay).toHaveBeenCalled()
    expect(t.onPause).not.toHaveBeenCalled()
    expect(t.elementTime()).toBe(30)

    expect(t.engine.setTimeline(URL_A, timeline)).toBe(true)

    expect(t.elementTime()).toBe(39) // where the 30 s paragraph is in this copy
    expect(t.el.paused).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.onTimelineHoldChange).toHaveBeenLastCalledWith(false)
    expect(t.engine.getCurrentTime()).toBe(30)
    expect(t.onPause).not.toHaveBeenCalled()
  })

  it('unlocks the element inside the click, for the start that comes later', () => {
    // Safari allows a play() outside a gesture only on an element that was
    // started inside one.
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    void t.engine.play()
    expect(t.play).toHaveBeenCalledTimes(1)
    expect(t.el.pause).toHaveBeenCalled()
    expect(t.el.paused).toBe(true)
  })

  it('never mutes, and leaves the listener’s volume and mute alone', () => {
    const t = clickedStart()
    t.engine.setMuted(true)
    expect(t.el.muted).toBe(true)
    t.engine.setVolume(0.4)
    expect(t.el.muted).toBe(false) // the slider unmutes, as it always does
    expect(t.el.volume).toBe(0.4)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.el.muted).toBe(false)
    expect(t.el.volume).toBe(0.4)
  })

  it('starts at the asked-for time, uncorrected, when told no map is coming', () => {
    const t = clickedStart()
    t.engine.releaseTimelineHold(URL_A)
    expect(t.elementTime()).toBe(30)
    expect(t.el.paused).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    // A map that turns up afterwards does not yank the audio.
    t.playTo(41)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(41)
  })

  it('gives up waiting after the cap', () => {
    const t = clickedStart()
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS - 1)
    expect(t.el.paused).toBe(true)
    vi.advanceTimersByTime(1)
    expect(t.el.paused).toBe(false)
    expect(t.elementTime()).toBe(30)
  })

  it('shows Play again when the browser refuses the late start', async () => {
    const t = clickedStart()
    t.refusePlay()
    t.engine.setTimeline(URL_A, timeline)
    await vi.advanceTimersByTimeAsync(0)
    expect(t.onPause).toHaveBeenCalledTimes(1)
    expect(t.engine.isPlaying()).toBe(false)
    expect(t.elementTime()).toBe(39) // in place: one tap plays the right words
  })

  it('holds a click made while already playing, before the map is in force', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    void t.engine.play()
    t.metadataLoaded()
    t.playTo(4)
    t.engine.seekTo(30, { holdForTimeline: true })
    expect(t.el.paused).toBe(true)
    expect(t.onPause).not.toHaveBeenCalled() // still playing, for the listener
    expect(t.engine.isPlaying()).toBe(true)
    // A second click replaces the first.
    t.engine.seekTo(10, { holdForTimeline: true })
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(15)
    expect(t.el.paused).toBe(false)
  })

  it('does not start a held click that was made while paused', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    t.metadataLoaded()
    t.engine.seekTo(30, { holdForTimeline: true })
    expect(t.engine.isPlaying()).toBe(false)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
    expect(t.el.paused).toBe(true)
    expect(t.play).not.toHaveBeenCalled()
  })

  it('does not hold once a map is in force: the seek is simply correct', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    t.metadataLoaded()
    t.engine.setTimeline(URL_A, timeline)
    t.engine.seekTo(30, { holdForTimeline: true })
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.elementTime()).toBe(39)
  })

  it('ends the hold and plays when the listener picks a place themselves', () => {
    const t = clickedStart()
    t.engine.seekTo(12) // the scrubber
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.elementTime()).toBe(12)
    expect(t.el.paused).toBe(false)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(12)
  })

  it('reports a pause during the wait, and still honours the click when the map arrives', () => {
    const t = clickedStart()
    t.engine.pause()
    expect(t.onPause).toHaveBeenCalledTimes(1)
    expect(t.engine.isPlaying()).toBe(false)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
    expect(t.el.paused).toBe(true) // paused is the listener's call
  })

  it('waits again when a paused held start is resumed before its map', () => {
    const t = clickedStart()
    t.engine.pause()
    void t.engine.play()
    // Resuming must not play the uncorrected position out loud.
    expect(t.el.paused).toBe(true)
    expect(t.engine.isHoldingForTimeline()).toBe(true)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
    expect(t.el.paused).toBe(false)

    // The wait is bounded again from the resume.
    const late = clickedStart()
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS - 100)
    late.engine.pause()
    void late.engine.play()
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS - 1)
    expect(late.el.paused).toBe(true)
    vi.advanceTimersByTime(1)
    expect(late.el.paused).toBe(false)

    // A hold that was released leaves nothing to hold on the next play.
    const released = clickedStart()
    released.engine.releaseTimelineHold(URL_A)
    released.engine.pause()
    void released.engine.play()
    expect(released.el.paused).toBe(false)
    expect(released.engine.isHoldingForTimeline()).toBe(false)
  })

  it('settles a paused held start when no map is coming, so resume just plays', () => {
    const t = clickedStart()
    t.engine.pause()
    t.play.mockClear()
    // The alignment ends without a map while the listener is paused.
    t.engine.releaseTimelineHold(URL_A)
    expect(t.elementTime()).toBe(30) // ready at the clicked position
    expect(t.play).not.toHaveBeenCalled() // still paused: their call

    void t.engine.play()
    expect(t.el.paused).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    // Nothing left for a late map to act on.
    t.playTo(36)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(36)
  })

  it('forgets a paused request once another paragraph is clicked', () => {
    const t = clickedStart() // the paragraph at 30 s
    t.engine.pause()
    t.engine.seekTo(10, { holdForTimeline: true }) // then another one
    void t.engine.play()
    // No map comes: the second click plays from where it was asked.
    t.engine.releaseTimelineHold(URL_A)
    expect(t.elementTime()).toBe(10)
    expect(t.el.paused).toBe(false)

    // An ordinary pause and resume afterwards is just that.
    t.playTo(14)
    t.engine.pause()
    void t.engine.play()
    expect(t.el.paused).toBe(false)
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.elementTime()).toBe(14)
  })

  it('reports the asked-for time as the audio position during the wait', () => {
    // What gets saved as the listening position.
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    void t.engine.play()
    expect(t.engine.getElementTime()).toBe(30) // before metadata, element still at 0
    t.metadataLoaded()
    t.engine.pause()
    expect(t.engine.getElementTime()).toBe(30)

    // Once the hold is settled the element's own clock is reported again.
    t.engine.releaseTimelineHold(URL_A)
    t.playTo(31)
    expect(t.engine.getElementTime()).toBe(31)

    const mapped = clickedStart()
    mapped.engine.setTimeline(URL_A, timeline)
    mapped.playTo(40)
    expect(mapped.engine.getElementTime()).toBe(40)
  })

  it('keeps holding across the reload that makes the audio checkable', () => {
    // First play on a host: loaded without crossorigin, then reloaded with it.
    const t = makeEngine()
    t.engine.load({ url: URL_A }, { seekTo: 30, holdForTimeline: true })
    void t.engine.play()
    t.metadataLoaded()
    t.onTimelineHoldChange.mockClear()

    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    void t.engine.play()
    expect(t.el.getAttribute('crossorigin')).toBe('anonymous')
    expect(t.el.paused).toBe(true)
    expect(t.onTimelineHoldChange).not.toHaveBeenCalled() // one hold, not two
    expect(t.engine.heldTranscriptTime()).toBe(30)
    t.engine.setTimeline(URL_A, timeline)
    expect(t.elementTime()).toBe(39)
    expect(t.el.paused).toBe(false)
    expect(t.onPause).not.toHaveBeenCalled()
  })

  it('stays paused through the fallback reload without crossorigin', () => {
    const t = clickedStart()
    t.play.mockClear()
    t.el.dispatchEvent(new Event('error')) // the host refused CORS
    expect(t.play).not.toHaveBeenCalled()
    expect(t.engine.isHoldingForTimeline()).toBe(true)
    t.engine.releaseTimelineHold(URL_A)
    expect(t.el.paused).toBe(false)
  })

  it('drops the hold with the source', () => {
    const t = clickedStart()
    t.play.mockClear()
    t.engine.load({ url: 'https://cdn.test/b.mp3' })
    expect(t.engine.isHoldingForTimeline()).toBe(false)
    expect(t.play).not.toHaveBeenCalled()

    const u = clickedStart()
    u.play.mockClear()
    u.engine.unload()
    vi.advanceTimersByTime(TIMELINE_HOLD_MAX_MS)
    expect(u.play).not.toHaveBeenCalled() // the cap no longer acts on an unloaded source
  })

  it('queues the corrected position when the map arrives before metadata', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 30, holdForTimeline: true })
    t.engine.setTimeline(URL_A, timeline)
    t.metadataLoaded()
    expect(t.elementTime()).toBe(39)
  })
})
