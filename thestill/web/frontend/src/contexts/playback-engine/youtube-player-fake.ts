// Shared test fixture for the YouTube IFrame Player API (spec #62).
//
// Test files mock `./youtube-iframe-api` to resolve `fakeYouTubeApi`
// instead of injecting a script tag; `FakeYTPlayer` records transport
// calls and lets tests drive state changes via `emit()`. Not a test file
// itself (no .test. in the name) — vitest never collects it.
import { vi } from 'vitest'

export interface FakeYTEvents {
  onReady?: (e: { data: number; target: unknown }) => void
  onStateChange?: (e: { data: number; target: unknown }) => void
  onError?: (e: { data: number; target: unknown }) => void
  onPlaybackRateChange?: (e: { data: number; target: unknown }) => void
}

export const fakePlayers: FakeYTPlayer[] = []

export function resetFakePlayers(): void {
  fakePlayers.length = 0
}

export class FakeYTPlayer {
  el: HTMLElement
  events: FakeYTEvents
  state = -1
  time = 0
  rate = 1
  duration = 3600
  playVideo = vi.fn(() => this.emit(1))
  pauseVideo = vi.fn(() => this.emit(2))
  seekTo = vi.fn((s: number) => {
    this.time = s
  })
  setPlaybackRate = vi.fn((r: number) => {
    this.rate = r
  })
  getPlaybackRate = () => this.rate
  getCurrentTime = vi.fn(() => this.time)
  getDuration = () => this.duration
  getPlayerState = () => this.state
  setVolume = vi.fn()
  mute = vi.fn()
  unMute = vi.fn()
  loadVideoById = vi.fn((_id: string, start?: number) => {
    this.time = start ?? 0
  })
  destroy = vi.fn()

  constructor(el: HTMLElement, opts: { events?: FakeYTEvents }) {
    this.el = el
    this.events = opts.events ?? {}
    fakePlayers.push(this)
    queueMicrotask(() => this.events.onReady?.({ data: 0, target: this }))
  }

  emit(state: number) {
    this.state = state
    this.events.onStateChange?.({ data: state, target: this })
  }
}

export const fakeYouTubeApi = {
  Player: FakeYTPlayer as unknown,
  PlayerState: { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 },
}
