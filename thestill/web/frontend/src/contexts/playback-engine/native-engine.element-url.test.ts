// The element may load another address than the source's own (an http
// enclosure played through our https redirect); the source keeps its own
// address as its identity. And a source that never loaded says so plainly.
import { describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { AUDIO_NOT_LOADED_MESSAGE, NativeEngine } from './native-engine'
import { TimelineMap } from './timeline-map'

const ENCLOSURE = 'http://open.example.org/a.mp3'
const VIA = '/api/podcasts/pod/episodes/ep/audio'

function makeEngine() {
  const el = document.createElement('video')
  let readyState = 0
  Object.defineProperty(el, 'readyState', { configurable: true, get: () => readyState })
  vi.spyOn(el, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(el, 'pause').mockImplementation(() => {})
  const onError = vi.fn()
  const noop = vi.fn()
  const events = new Proxy({} as EngineEvents, { get: (_t, name) => (name === 'onError' ? onError : noop) })
  const engine = new NativeEngine(el, events)
  return {
    el,
    engine,
    onError,
    setReadyState: (value: number) => {
      readyState = value
    },
    fail: (code: number) => {
      Object.defineProperty(el, 'error', { configurable: true, get: () => ({ code }) })
      el.dispatchEvent(new Event('error'))
    },
  }
}

describe('NativeEngine element address', () => {
  it('loads the element address and keeps the source address as its identity', () => {
    const { el, engine } = makeEngine()
    engine.load({ url: ENCLOSURE, elementUrl: VIA })
    expect(el.getAttribute('src')).toBe(VIA)
    // A map is offered for the source's own address, never the element's.
    const timeline = { map: TimelineMap.constant(0), durationS: 10 }
    expect(engine.setTimeline(VIA, timeline)).toBe(false)
    expect(engine.setTimeline(ENCLOSURE, timeline)).toBe(true)
  })

  it('loads the source address when no other is given, and forgets one on the next load', () => {
    const { el, engine } = makeEngine()
    engine.load({ url: ENCLOSURE, elementUrl: VIA })
    engine.load({ url: 'https://cdn.example.net/b.mp3' })
    expect(el.getAttribute('src')).toBe('https://cdn.example.net/b.mp3')
  })

  it('hands the element address to the engine that replaces it', () => {
    const { engine } = makeEngine()
    engine.load({ url: ENCLOSURE, elementUrl: VIA })
    expect(engine.destroy()).toMatchObject({ url: ENCLOSURE, elementUrl: VIA })
  })

  it('says the audio could not be loaded when none of it arrived, whatever the code', () => {
    const { engine, fail, onError } = makeEngine()
    engine.load({ url: ENCLOSURE, elementUrl: VIA })
    fail(4) // what a refused or unreachable address is reported as
    expect(onError).toHaveBeenLastCalledWith(AUDIO_NOT_LOADED_MESSAGE)
  })

  it('keeps the specific message once the source had loaded', () => {
    const { engine, fail, onError, setReadyState } = makeEngine()
    engine.load({ url: ENCLOSURE })
    setReadyState(2)
    fail(3)
    expect(onError).toHaveBeenLastCalledWith('The media could not be decoded.')
  })
})
