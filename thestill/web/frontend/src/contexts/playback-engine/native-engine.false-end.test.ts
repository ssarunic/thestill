// Safari's false end (seen 2026-11-11): after a seek back from the real end
// of an MP3 it reports the seek target as the duration and fires `ended`.
import { describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { NativeEngine } from './native-engine'

const URL_A = 'https://cdn.test/a.mp3'

function makeEngine() {
  const el = document.createElement('video')
  const state = { currentTime: 0, duration: 965.3, ended: false, seeking: false, paused: true }
  for (const key of ['duration', 'ended', 'seeking', 'paused'] as const) {
    Object.defineProperty(el, key, { configurable: true, get: () => state[key] })
  }
  Object.defineProperty(el, 'readyState', { configurable: true, get: () => HTMLMediaElement.HAVE_METADATA })
  Object.defineProperty(el, 'currentTime', {
    configurable: true,
    get: () => state.currentTime,
    set: (v: number) => {
      state.currentTime = v
      state.seeking = true
      el.dispatchEvent(new Event('seeking'))
    },
  })
  const srcSet = vi.fn()
  Object.defineProperty(el, 'src', { configurable: true, get: () => URL_A, set: srcSet })
  const play = vi.fn(async () => {
    state.paused = false
  })
  el.play = play
  el.pause = vi.fn(() => {
    state.paused = true
  })
  const onEnded = vi.fn()
  const onPause = vi.fn()
  const noop = vi.fn()
  const named: Record<string, unknown> = { onEnded, onPause }
  const events = new Proxy({} as EngineEvents, { get: (_t, name) => named[name as string] ?? noop })
  const engine = new NativeEngine(el, events)
  engine.load({ url: URL_A })
  srcSet.mockClear()
  el.dispatchEvent(new Event('durationchange'))
  const seeked = () => {
    state.seeking = false
    el.dispatchEvent(new Event('seeked'))
  }
  /** What Safari does: the duration becomes the position, then pause + ended. */
  const endHere = () => {
    state.duration = state.currentTime
    state.ended = true
    state.paused = true
    el.dispatchEvent(new Event('durationchange'))
    el.dispatchEvent(new Event('pause'))
    el.dispatchEvent(new Event('ended'))
  }
  return { el, engine, state, play, srcSet, onEnded, onPause, seeked, endHere }
}

describe('NativeEngine and an end the browser made up', () => {
  it('reloads the source at the seek target instead of reporting an end', () => {
    const t = makeEngine()
    void t.engine.play()
    t.engine.seekTo(913.27)
    t.seeked()
    t.play.mockClear()
    t.endHere()

    expect(t.onEnded).not.toHaveBeenCalled()
    expect(t.onPause).not.toHaveBeenCalled()
    expect(t.srcSet).toHaveBeenCalledWith(URL_A)
    expect(t.play).toHaveBeenCalledTimes(1)
    // The position is put back once the fresh load has its metadata.
    t.state.currentTime = 0
    t.state.ended = false
    t.el.dispatchEvent(new Event('loadedmetadata'))
    expect(t.state.currentTime).toBe(913.27)
  })

  it('believes an end that comes back at the same place', () => {
    const t = makeEngine()
    void t.engine.play()
    t.engine.seekTo(913.27)
    t.seeked()
    t.endHere()
    t.srcSet.mockClear()
    t.el.dispatchEvent(new Event('loadedmetadata'))
    t.seeked()
    t.endHere()
    expect(t.srcSet).not.toHaveBeenCalled()
    expect(t.onEnded).toHaveBeenCalledTimes(1)
  })

  it('reports a real end: played through, or a seek to the very end', () => {
    const t = makeEngine()
    void t.engine.play()
    t.engine.seekTo(900)
    t.seeked()
    t.state.currentTime = 965.36 // played on to the end
    t.endHere()
    expect(t.onEnded).toHaveBeenCalledTimes(1)
    expect(t.srcSet).not.toHaveBeenCalled()

    const u = makeEngine()
    void u.engine.play()
    u.engine.seekTo(964.9)
    u.seeked()
    u.endHere()
    expect(u.onEnded).toHaveBeenCalledTimes(1)
    expect(u.srcSet).not.toHaveBeenCalled()
  })

  it('starts a seek made after the end only once the seek has landed', async () => {
    const t = makeEngine()
    void t.engine.play()
    t.state.currentTime = 965.3
    t.state.ended = true
    t.el.dispatchEvent(new Event('ended'))
    t.play.mockClear()

    t.engine.seekTo(913.27)
    const started = t.engine.play()
    expect(t.play).not.toHaveBeenCalled()
    t.state.ended = false
    t.seeked()
    await started
    expect(t.play).toHaveBeenCalledTimes(1)

    // Play on an ended element with no seek restarts at once, as always.
    const u = makeEngine()
    u.state.currentTime = 965.3
    u.state.ended = true
    u.el.dispatchEvent(new Event('ended'))
    void u.engine.play()
    expect(u.play).toHaveBeenCalledTimes(1)
  })
})
