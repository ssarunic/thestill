// Spec #90 — NativeEngine.setPendingSeek: a resume position learned after
// the source was assigned.
import { describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { NativeEngine } from './native-engine'

function makeEngine() {
  const el = document.createElement('video')
  let readyState = 0
  let currentTime = 0
  Object.defineProperty(el, 'readyState', { configurable: true, get: () => readyState })
  Object.defineProperty(el, 'currentTime', {
    configurable: true,
    get: () => currentTime,
    set: (v: number) => {
      currentTime = v
    },
  })
  const noop = vi.fn()
  const events = new Proxy({} as EngineEvents, { get: () => noop })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    metadataLoaded: () => {
      readyState = HTMLMediaElement.HAVE_METADATA
      el.dispatchEvent(new Event('loadedmetadata'))
    },
    time: () => currentTime,
  }
}

describe('NativeEngine.setPendingSeek', () => {
  it('replaces the deferred seek before metadata arrives', () => {
    const t = makeEngine()
    t.engine.load({ url: 'https://cdn.test/a.mp3' })
    expect(t.engine.setPendingSeek(1934)).toBe(true)
    expect(t.time()).toBe(0)
    t.metadataLoaded()
    expect(t.time()).toBe(1934)
  })

  it('seeks immediately after metadata while still near the start', () => {
    const t = makeEngine()
    t.engine.load({ url: 'https://cdn.test/a.mp3' })
    t.metadataLoaded()
    t.el.currentTime = 3
    expect(t.engine.setPendingSeek(1934)).toBe(true)
    expect(t.time()).toBe(1934)
  })

  it('never overrides a user seek or a source already past 5 s', () => {
    const t = makeEngine()
    t.engine.load({ url: 'https://cdn.test/a.mp3' })
    t.metadataLoaded()
    t.engine.seekTo(1)
    expect(t.engine.setPendingSeek(1934)).toBe(false)
    expect(t.time()).toBe(1)

    t.engine.load({ url: 'https://cdn.test/b.mp3' })
    t.metadataLoaded()
    t.el.currentTime = 6
    expect(t.engine.setPendingSeek(1934)).toBe(false)
  })

  it('does nothing without a source', () => {
    const t = makeEngine()
    expect(t.engine.setPendingSeek(10)).toBe(false)
  })
})
