// Spec #113 — NativeEngine HLS: native where the browser plays HLS, hls.js
// on the same element everywhere else.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'
import { NativeEngine } from './native-engine'
import { isHlsSource } from './hls'

const { FakeHls } = vi.hoisted(() => {
  type Handler = (event: string, data: unknown) => void
  class FakeHls {
    static Events = { ERROR: 'hlsError' }
    static supported = true
    static isSupported = () => FakeHls.supported
    static instances: FakeHls[] = []

    config: Record<string, unknown>
    media: HTMLMediaElement | null = null
    source: string | null = null
    destroyed = false
    recoveries = 0
    handlers = new Map<string, Set<Handler>>()

    constructor(config: Record<string, unknown>) {
      this.config = config
      FakeHls.instances.push(this)
    }
    on(event: string, handler: Handler) {
      if (!this.handlers.has(event)) this.handlers.set(event, new Set())
      this.handlers.get(event)!.add(handler)
    }
    off(event: string, handler: Handler) {
      this.handlers.get(event)?.delete(handler)
    }
    attachMedia(el: HTMLMediaElement) {
      this.media = el
    }
    loadSource(url: string) {
      this.source = url
    }
    recoverMediaError() {
      this.recoveries++
    }
    destroy() {
      this.destroyed = true
    }
    emitError(data: { fatal: boolean; type: string }) {
      for (const handler of this.handlers.get('hlsError') ?? []) handler('hlsError', data)
    }
  }
  return { FakeHls }
})

vi.mock('hls.js/light', () => ({ default: FakeHls }))

const HLS_URL = 'https://episode.flightcast.com/hls/v/ep.m3u8'
const HLS_MIME = 'application/x-mpegURL'
const MP3_URL = 'https://cdn.test/ep.mp3'

function spyEvents() {
  const spies = new Map<string, ReturnType<typeof vi.fn>>()
  const events = new Proxy({} as EngineEvents, {
    get: (_t, key: string) => {
      if (!spies.has(key)) spies.set(key, vi.fn())
      return spies.get(key)
    },
  })
  return { events, spy: (key: keyof EngineEvents) => events[key] as unknown as ReturnType<typeof vi.fn> }
}

function makeEngine({ nativeHls = false } = {}) {
  const el = document.createElement('video')
  el.canPlayType = vi.fn((type: string) => (nativeHls && type === 'application/vnd.apple.mpegurl' ? 'maybe' : ''))
  el.play = vi.fn(() => Promise.resolve())
  el.pause = vi.fn()
  el.load = vi.fn()
  const { events, spy } = spyEvents()
  const engine = new NativeEngine(el, events)
  return { el, engine, events, spy }
}

// Let the dynamic imports and attach promise chain settle.
async function flush() {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  FakeHls.instances = []
  FakeHls.supported = true
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('isHlsSource', () => {
  it('matches HLS MIME types case-insensitively, else the .m3u8 path', () => {
    expect(isHlsSource(HLS_URL, 'application/x-mpegURL')).toBe(true)
    expect(isHlsSource(HLS_URL, 'APPLICATION/VND.APPLE.MPEGURL')).toBe(true)
    expect(isHlsSource('https://cdn.test/a.m3u8?sig=1', null)).toBe(true)
    expect(isHlsSource(MP3_URL, 'audio/mpeg')).toBe(false)
    expect(isHlsSource(MP3_URL, null)).toBe(false)
    // An explicit MIME type wins over the path.
    expect(isHlsSource('https://cdn.test/a.m3u8', 'video/mp4')).toBe(false)
  })
})

describe('NativeEngine HLS', () => {
  it('attaches hls.js when the browser has no native HLS', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME }, { seekTo: 120 })
    expect(t.el.getAttribute('src')).toBeNull()
    await flush()

    expect(FakeHls.instances).toHaveLength(1)
    const hls = FakeHls.instances[0]
    expect(hls.media).toBe(t.el)
    expect(hls.source).toBe(HLS_URL)
    expect(hls.config.capLevelToPlayerSize).toBe(true)
    expect(hls.config.startPosition).toBe(120)
    // Same-origin worker asset, never the inline blob: worker.
    expect(typeof hls.config.workerPath).toBe('string')
    expect(hls.config.workerPath).not.toMatch(/^blob:/)
  })

  it('plays HLS natively where canPlayType says so (Safari/iOS)', async () => {
    const t = makeEngine({ nativeHls: true })
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    expect(t.el.getAttribute('src')).toBe(HLS_URL)
    expect(FakeHls.instances).toHaveLength(0)
  })

  it('leaves non-HLS sources on the plain src path', async () => {
    const t = makeEngine()
    t.engine.load({ url: MP3_URL, mimeType: 'audio/mpeg' })
    await flush()
    expect(t.el.getAttribute('src')).toBe(MP3_URL)
    expect(FakeHls.instances).toHaveLength(0)
  })

  it('stops the outgoing file before waiting for the hls.js chunk', () => {
    const t = makeEngine()
    t.engine.load({ url: MP3_URL, mimeType: 'audio/mpeg' })
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    expect(t.el.getAttribute('src')).toBeNull()
    expect(t.el.load).toHaveBeenCalled()
  })

  it('drops an attach superseded before the chunk lands', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    t.engine.load({ url: MP3_URL, mimeType: 'audio/mpeg' })
    await flush()
    expect(FakeHls.instances).toHaveLength(0)
    expect(t.el.getAttribute('src')).toBe(MP3_URL)
  })

  it('destroys hls.js when the source changes or the session unloads', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    t.engine.load({ url: MP3_URL, mimeType: 'audio/mpeg' })
    expect(FakeHls.instances[0].destroyed).toBe(true)

    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    t.engine.unload()
    expect(FakeHls.instances[1].destroyed).toBe(true)
  })

  it('play() during a pending attach waits for it', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    const played = t.engine.play()
    expect(t.el.play).not.toHaveBeenCalled()
    await played
    expect(FakeHls.instances).toHaveLength(1)
    expect(t.el.play).toHaveBeenCalledTimes(1)
  })

  it('recovers one fatal media error, then reports the next', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    const hls = FakeHls.instances[0]

    hls.emitError({ fatal: false, type: 'mediaError' })
    hls.emitError({ fatal: true, type: 'mediaError' })
    expect(hls.recoveries).toBe(1)
    expect(t.spy('onError')).not.toHaveBeenCalled()

    hls.emitError({ fatal: true, type: 'mediaError' })
    expect(t.spy('onError')).toHaveBeenCalledWith('The media could not be decoded.')
    expect(hls.destroyed).toBe(true)
  })

  it('reports a fatal network error', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    FakeHls.instances[0].emitError({ fatal: true, type: 'networkError' })
    expect(t.spy('onError')).toHaveBeenCalledWith('A network error interrupted playback.')
  })

  it('reports a browser without Media Source Extensions', async () => {
    FakeHls.supported = false
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    expect(FakeHls.instances).toHaveLength(0)
    expect(t.spy('onError')).toHaveBeenCalledWith('This media format is not supported by your browser.')
  })

  it('hands an attached instance to a replacement engine (StrictMode remount)', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    await flush()
    const hls = FakeHls.instances[0]

    const next = spyEvents()
    const successor = new NativeEngine(t.el, next.events, t.engine.destroy())
    expect(hls.destroyed).toBe(false)

    hls.emitError({ fatal: true, type: 'networkError' })
    expect(t.spy('onError')).not.toHaveBeenCalled()
    expect(next.spy('onError')).toHaveBeenCalledWith('A network error interrupted playback.')

    successor.unload()
    expect(hls.destroyed).toBe(true)
  })

  it('a replacement engine finishes an attach its predecessor started', async () => {
    const t = makeEngine()
    t.engine.load({ url: HLS_URL, mimeType: HLS_MIME })
    const next = spyEvents()
    new NativeEngine(t.el, next.events, t.engine.destroy())
    await flush()

    expect(FakeHls.instances).toHaveLength(1)
    FakeHls.instances[0].emitError({ fatal: true, type: 'networkError' })
    expect(t.spy('onError')).not.toHaveBeenCalled()
    expect(next.spy('onError')).toHaveBeenCalledTimes(1)
  })
})
