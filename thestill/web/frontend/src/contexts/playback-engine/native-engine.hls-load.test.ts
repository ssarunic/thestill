// Spec #113 — a failed hls.js chunk download is retryable (Codex #466).
// Separate file: loadHls is mocked here so its result can be controlled per
// call; the main HLS suite uses the real (memoised) loader.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineEvents } from './types'

const { loadHls, FakeHls } = vi.hoisted(() => {
  class FakeHls {
    static isSupported = () => true
    static instances: FakeHls[] = []
    config: Record<string, unknown>
    source: string | null = null
    constructor(config: Record<string, unknown>) {
      this.config = config
      FakeHls.instances.push(this)
    }
    on() {}
    off() {}
    attachMedia() {}
    loadSource(url: string) {
      this.source = url
    }
    destroy() {}
  }
  return { loadHls: vi.fn(), FakeHls }
})

vi.mock('./hls', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./hls')>()),
  loadHls,
}))

import { NativeEngine } from './native-engine'

const HLS_URL = 'https://episode.flightcast.com/hls/v/ep.m3u8'

async function flush() {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  FakeHls.instances = []
  loadHls.mockReset()
})

describe('NativeEngine — hls.js chunk load failure', () => {
  it('reports the failure, then retries the download on the next play()', async () => {
    const el = document.createElement('video')
    el.canPlayType = () => ''
    el.play = vi.fn(() => Promise.resolve())
    el.load = vi.fn()
    const onError = vi.fn()
    const events = new Proxy({} as EngineEvents, { get: (_t, key) => (key === 'onError' ? onError : vi.fn()) })
    const engine = new NativeEngine(el, events)

    loadHls.mockRejectedValueOnce(new Error('chunk failed'))
    engine.load({ url: HLS_URL, mimeType: 'application/x-mpegURL' }, { seekTo: 300 })
    await flush()
    expect(onError).toHaveBeenCalledWith('The video player could not be loaded.')
    expect(engine.getCurrentTime()).toBe(300)

    loadHls.mockResolvedValueOnce({ Hls: FakeHls, workerPath: '/assets/hls.worker.js' })
    await engine.play()
    expect(loadHls).toHaveBeenCalledTimes(2)
    expect(FakeHls.instances).toHaveLength(1)
    expect(FakeHls.instances[0].source).toBe(HLS_URL)
    expect(FakeHls.instances[0].config.startPosition).toBe(300)
    expect(el.play).toHaveBeenCalledTimes(1)
  })

  it('never holds an HLS stream for a timeline map (spec #116)', async () => {
    // No map can be made for a stream, so a hold would only be silence.
    const el = document.createElement('video')
    el.canPlayType = () => ''
    el.play = vi.fn(() => Promise.resolve())
    el.load = vi.fn()
    const onTimelineHoldChange = vi.fn()
    const events = new Proxy({} as EngineEvents, {
      get: (_t, key) => (key === 'onTimelineHoldChange' ? onTimelineHoldChange : vi.fn()),
    })
    const engine = new NativeEngine(el, events)
    loadHls.mockResolvedValue({ Hls: FakeHls, workerPath: '/assets/hls.worker.js' })

    engine.load({ url: HLS_URL, mimeType: 'application/x-mpegURL' }, { seekTo: 30, holdForTimeline: true })
    expect(el.muted).toBe(false)
    await flush()
    engine.seekTo(60, { holdForTimeline: true })
    expect(el.muted).toBe(false)
    expect(engine.isHoldingForTimeline()).toBe(false)
    expect(onTimelineHoldChange).not.toHaveBeenCalled()
  })
})
