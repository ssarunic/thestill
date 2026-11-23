// Spec #116 §4 step 6 — loading with `crossorigin`, and the one fallback
// to a plain load when the host turns out not to allow it.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getCorsVerdict, setCorsVerdict } from '../../drift/corsVerdict'
import type { EngineEvents } from './types'
import { NativeEngine } from './native-engine'

const URL_A = 'https://cdn.test/a.mp3'

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
  const play = vi.fn(async () => {})
  el.play = play
  el.pause = vi.fn()
  const onError = vi.fn()
  const noop = vi.fn()
  const events = new Proxy({} as EngineEvents, { get: (_t, name) => (name === 'onError' ? onError : noop) })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    onError,
    play,
    fail: () => el.dispatchEvent(new Event('error')),
    metadataLoaded: () => {
      readyState = HTMLMediaElement.HAVE_METADATA
      el.dispatchEvent(new Event('loadedmetadata'))
    },
    time: () => currentTime,
  }
}

beforeEach(() => localStorage.clear())

describe('NativeEngine crossorigin', () => {
  it('sets the attribute before the source, only when asked', () => {
    const t = makeEngine()
    // What the element looked like at the moment its source was assigned.
    let attributeAtAssign: string | null | undefined
    Object.defineProperty(t.el, 'src', {
      configurable: true,
      set(value: string) {
        attributeAtAssign = t.el.getAttribute('crossorigin')
        t.el.setAttribute('src', value)
      },
      get: () => t.el.getAttribute('src') ?? '',
    })
    t.engine.load({ url: URL_A, crossOrigin: true })
    expect(attributeAtAssign).toBe('anonymous')
    expect(t.el.getAttribute('crossorigin')).toBe('anonymous')

    t.engine.load({ url: 'https://cdn.test/b.mp3' })
    expect(t.el.hasAttribute('crossorigin')).toBe(false)
  })

  it('reloads once without the attribute when the load fails, keeping position and play', () => {
    const t = makeEngine()
    setCorsVerdict(URL_A, 'cors_ok')
    t.engine.load({ url: URL_A, crossOrigin: true }, { seekTo: 1234 })
    void t.engine.play()
    t.play.mockClear()

    t.fail()

    expect(t.onError).not.toHaveBeenCalled()
    expect(t.el.hasAttribute('crossorigin')).toBe(false)
    expect(t.el.getAttribute('src')).toBe(URL_A)
    expect(t.play).toHaveBeenCalledTimes(1)
    expect(getCorsVerdict(URL_A)).toBe('no_cors')
    // The resume position still applies to the plain load.
    t.metadataLoaded()
    expect(t.time()).toBe(1234)

    // A second failure is a real one.
    t.fail()
    expect(t.onError).toHaveBeenCalledTimes(1)
  })

  it('does not start playing on the fallback when nothing asked for play', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    t.fail()
    expect(t.play).not.toHaveBeenCalled()
    expect(t.onError).not.toHaveBeenCalled()
  })

  it('reports a failure of a plain load once playback was asked for', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A })
    void t.engine.play()
    t.fail()
    expect(t.onError).toHaveBeenCalledTimes(1)
  })

  it('says nothing when a source that was only preloaded fails', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A })
    t.fail()
    expect(t.onError).not.toHaveBeenCalled()
  })

  it('drops the attribute when the source is unloaded', () => {
    const t = makeEngine()
    t.engine.load({ url: URL_A, crossOrigin: true })
    t.engine.unload()
    expect(t.el.hasAttribute('crossorigin')).toBe(false)
    t.fail()
    expect(t.onError).not.toHaveBeenCalled()
  })
})
