// Spec #116 §5 — a verified timeline map in the native engine. With one
// set, the engine takes and reports transcript time; the audio itself does
// not move when the map goes on or off.
import { describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { NativeEngine, type AppliedTimeline } from './native-engine'
import { TimelineMap } from './timeline-map'

const URL_A = 'https://cdn.test/a.mp3'

// The listener's copy: an 8 s pre-roll, 20 s of episode, a 15 s ad where
// the transcribed copy had a 6 s one, then the rest (transcript 26–46 s).
const timeline: AppliedTimeline = {
  map: new TimelineMap(
    [
      { srcStartS: 0, srcEndS: 20, offsetS: 8 },
      { srcStartS: 26, srcEndS: 46, offsetS: 17 },
    ],
    [{ srcStartS: 20, srcEndS: 26, kind: 'confirmed' }],
  ),
  durationS: 46,
}

function makeEngine() {
  const el = document.createElement('video')
  let readyState = 0
  let currentTime = 0
  let duration = NaN
  Object.defineProperty(el, 'readyState', { configurable: true, get: () => readyState })
  Object.defineProperty(el, 'duration', { configurable: true, get: () => duration })
  Object.defineProperty(el, 'currentTime', {
    configurable: true,
    get: () => currentTime,
    set: (v: number) => {
      currentTime = v
    },
  })
  const onTimeUpdate = vi.fn()
  const onDurationChange = vi.fn()
  const noop = vi.fn()
  const events = new Proxy({} as EngineEvents, {
    get: (_t, name) => (name === 'onTimeUpdate' ? onTimeUpdate : name === 'onDurationChange' ? onDurationChange : noop),
  })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    onTimeUpdate,
    onDurationChange,
    metadataLoaded: (seconds: number) => {
      readyState = HTMLMediaElement.HAVE_METADATA
      duration = seconds
      el.dispatchEvent(new Event('loadedmetadata'))
      el.dispatchEvent(new Event('durationchange'))
    },
    /** The element's own clock. */
    elementTime: () => currentTime,
    playTo: (seconds: number) => {
      currentTime = seconds
      el.dispatchEvent(new Event('timeupdate'))
    },
  }
}

function loaded() {
  const t = makeEngine()
  t.engine.load({ url: URL_A, crossOrigin: true })
  t.metadataLoaded(63)
  return t
}

describe('NativeEngine timeline map', () => {
  it('reports transcript time from the moment the map is set, without moving the audio', () => {
    const t = loaded()
    t.playTo(50) // the element is 50 s into the listener's copy
    expect(t.engine.getCurrentTime()).toBe(50)

    expect(t.engine.setTimeline(URL_A, timeline)).toBe(true)

    expect(t.elementTime()).toBe(50)
    expect(t.engine.getCurrentTime()).toBe(33) // 50 − 17: the words being spoken
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(33)
    expect(t.engine.getDuration()).toBe(46)
    expect(t.onDurationChange).toHaveBeenLastCalledWith(46)
    expect(t.engine.hasTimeline()).toBe(true)
  })

  it('seeks to where the asked-for words are in this copy', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.engine.seekTo(30) // a paragraph at 30 s of the transcript
    expect(t.elementTime()).toBe(47)
    expect(t.engine.getCurrentTime()).toBe(30)
    t.engine.seekTo(10)
    expect(t.elementTime()).toBe(18)
  })

  it('sends a seek into an ad of the transcribed copy to what follows it', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.engine.seekTo(23)
    expect(t.elementTime()).toBe(43) // the start of the second span in this copy
    expect(t.engine.getCurrentTime()).toBe(26)
  })

  it('holds the transcript clock through an ad that only this copy has', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.playTo(3) // pre-roll
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(0)
    t.playTo(27.9)
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(27.9 - 8)
    t.playTo(35) // mid-roll: the clock waits where the episode stopped
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(20)
    t.playTo(42.9)
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(20)
    t.playTo(44)
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(27)
  })

  it('goes back to the element’s own clock when the map is lifted', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.playTo(50)
    expect(t.engine.setTimeline(URL_A, null)).toBe(true)
    expect(t.elementTime()).toBe(50)
    expect(t.engine.getCurrentTime()).toBe(50)
    expect(t.onTimeUpdate).toHaveBeenLastCalledWith(50)
    expect(t.engine.getDuration()).toBe(63)
    t.engine.seekTo(30)
    expect(t.elementTime()).toBe(30)
  })

  it('refuses a map for a source that is not the loaded one', () => {
    const t = loaded()
    expect(t.engine.setTimeline('https://cdn.test/other.mp3', timeline)).toBe(false)
    expect(t.engine.hasTimeline()).toBe(false)
  })

  it('drops the map with the source', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.engine.load({ url: 'https://cdn.test/b.mp3' })
    expect(t.engine.hasTimeline()).toBe(false)
    t.engine.seekTo(30)
    expect(t.elementTime()).toBe(30)

    t.engine.load({ url: URL_A, crossOrigin: true })
    t.engine.setTimeline(URL_A, timeline)
    t.engine.unload()
    expect(t.engine.hasTimeline()).toBe(false)
  })

  it('drops the map when the load falls back from crossorigin', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.el.dispatchEvent(new Event('error'))
    expect(t.engine.hasTimeline()).toBe(false)
  })

  it('hands the map to a replacement engine on the same element', () => {
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    const state = t.engine.destroy()
    const noop = vi.fn()
    const next = new NativeEngine(t.el, new Proxy({} as EngineEvents, { get: () => noop }), state)
    expect(next.hasTimeline()).toBe(true)
    next.seekTo(30)
    expect(t.elementTime()).toBe(47)
  })

  it('keeps the audio’s own position and duration readable under a map', () => {
    // What a saved listening position holds: a resume seeks before any map
    // exists, so it must be a position in the audio itself.
    const t = loaded()
    t.engine.setTimeline(URL_A, timeline)
    t.playTo(50)
    expect(t.engine.getCurrentTime()).toBe(33)
    expect(t.engine.getElementTime()).toBe(50)
    expect(t.engine.getElementDuration()).toBe(63)
  })

  it('treats a resume position as a position in the audio', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 50 })
    t.metadataLoaded(63)
    expect(t.elementTime()).toBe(50)

    // A late resume position (spec #90) arriving after a map is set.
    const late = makeEngine()
    late.engine.load({ url: URL_A, crossOrigin: true })
    late.metadataLoaded(63)
    late.engine.setTimeline(URL_A, timeline)
    expect(late.engine.setPendingSeek(50)).toBe(true)
    expect(late.elementTime()).toBe(50)
  })
})
