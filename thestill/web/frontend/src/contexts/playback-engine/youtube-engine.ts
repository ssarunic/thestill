// Spec #62 §5 — YouTubeEngine: the IFrame Player API behind the
// PlaybackEngine interface.
//
// The player is constructed asynchronously (script load + onReady), so
// load() queues its intent and applies it when the player becomes ready.
// The clock is polled every ~250 ms and linearly interpolated between
// samples while playing (spec #62 §8) so the karaoke rAF consumers keep
// their synchronous getCurrentTime() contract; during ads/buffering the
// clock freezes rather than drifting, and seeks reset the interpolation
// baseline immediately ("correcting after seeks").
//
// Compliance note (§7): this engine never plays without its iframe being
// visibly presented — that policy lives in PlayerContext's presentation
// effect, not here — and nothing is ever downloaded or cached.

import type { EngineEvents, EngineSource, LoadOptions, PlaybackEngine } from './types'
import { loadYouTubeIframeApi, type YTNamespace, type YTPlayer } from './youtube-iframe-api'

const POLL_INTERVAL_MS = 250

// IFrame API error codes → user-facing messages.
function describeYouTubeError(code: number): string {
  switch (code) {
    case 2:
      return 'The YouTube video request was invalid.'
    case 5:
      return 'The YouTube player failed to start.'
    case 100:
      return 'This YouTube video was removed or is private.'
    case 101:
    case 150:
      return 'This video cannot be embedded outside YouTube.'
    default:
      return 'YouTube playback failed.'
  }
}

interface PendingLoad {
  videoId: string
  seekTo: number | null
  rate: number | null
  autoplay: boolean
}

export class YouTubeEngine implements PlaybackEngine {
  readonly kind = 'youtube' as const

  private container: HTMLElement
  private events: EngineEvents
  private player: YTPlayer | null = null
  private yt: YTNamespace | null = null
  private ready = false
  private destroyed = false
  private pending: PendingLoad | null = null
  private currentVideoId: string | null = null
  private pollHandle: ReturnType<typeof setInterval> | null = null
  // Interpolation baseline: last sampled player time + the wall-clock
  // moment it was taken. `playing` gates extrapolation.
  private sample = { t: 0, wall: 0 }
  private playing = false
  private rate = 1

  constructor(container: HTMLElement, events: EngineEvents) {
    this.container = container
    this.events = events
  }

  load(source: EngineSource, opts?: LoadOptions): void {
    if (!source.videoId) return
    this.pending = {
      videoId: source.videoId,
      seekTo: opts?.seekTo ?? null,
      rate: opts?.rate ?? null,
      autoplay: opts?.autoplay ?? false,
    }
    this.resample(opts?.seekTo ?? 0)

    if (this.player && this.ready) {
      this.applyPending()
      return
    }
    if (this.player) return // constructing — onReady applies this.pending

    void loadYouTubeIframeApi()
      .then((yt) => {
        if (this.destroyed) return
        this.yt = yt
        // The API REPLACES its target element with the iframe, so the
        // player is constructed on a throwaway inner div — the stable
        // host (owned by React) is never replaced, and destroy/recreate
        // cycles keep working. YouTube's own controls are kept (v1 — our
        // transport proxies via the API, never strips the player chrome).
        const target = document.createElement('div')
        target.style.width = '100%'
        target.style.height = '100%'
        this.container.replaceChildren(target)
        this.player = new yt.Player(target, {
          playerVars: {
            playsinline: 1,
            fs: 1,
            origin: window.location.origin,
          },
          events: {
            onReady: () => this.handleReady(),
            onStateChange: (e) => this.handleStateChange(e.data),
            onError: (e) => this.events.onError(describeYouTubeError(e.data)),
            onPlaybackRateChange: (e) => {
              this.rate = typeof e.data === 'number' && e.data > 0 ? e.data : this.player?.getPlaybackRate() ?? 1
              this.resample(this.safeCall(() => this.player?.getCurrentTime()) ?? this.sample.t)
              this.events.onRateChange(this.rate)
            },
          },
        })
        this.events.onWaiting()
      })
      .catch((e: unknown) => {
        if (this.destroyed) return
        this.events.onError(e instanceof Error ? e.message : 'Failed to load the YouTube player.')
      })
  }

  private handleReady(): void {
    this.ready = true
    if (this.pending) this.applyPending()
  }

  private applyPending(): void {
    const pending = this.pending
    if (!pending || !this.player) return
    this.pending = null
    this.safeCall(() => {
      if (this.currentVideoId !== pending.videoId) {
        this.currentVideoId = pending.videoId
        this.player!.loadVideoById(pending.videoId, pending.seekTo ?? 0)
        if (!pending.autoplay) this.player!.pauseVideo()
      } else if (pending.seekTo != null) {
        this.player!.seekTo(pending.seekTo, true)
      }
      if (pending.rate != null && pending.rate > 0) this.player!.setPlaybackRate(pending.rate)
      if (pending.autoplay) this.player!.playVideo()
    })
    this.resample(pending.seekTo ?? 0)
    const duration = this.safeCall(() => this.player!.getDuration())
    if (duration && Number.isFinite(duration) && duration > 0) this.events.onDurationChange(duration)
    this.events.onCanPlay()
  }

  private handleStateChange(state: number): void {
    const states = this.yt?.PlayerState
    if (!states) return
    // Every transition re-samples so interpolation never extrapolates
    // across a state boundary (ads report BUFFERING/PAUSED — the clock
    // freezes instead of drifting).
    this.resample(this.safeCall(() => this.player?.getCurrentTime()) ?? this.sample.t)
    switch (state) {
      case states.PLAYING: {
        this.playing = true
        this.startPolling()
        const duration = this.safeCall(() => this.player?.getDuration())
        if (duration && Number.isFinite(duration) && duration > 0) this.events.onDurationChange(duration)
        this.events.onPlaying()
        this.events.onPlay()
        break
      }
      case states.PAUSED:
        this.playing = false
        this.stopPolling()
        this.events.onPause()
        break
      case states.BUFFERING:
        this.playing = false
        this.events.onWaiting()
        break
      case states.ENDED:
        this.playing = false
        this.stopPolling()
        this.events.onEnded()
        break
      case states.CUED:
        this.playing = false
        this.events.onCanPlay()
        break
      default:
        break
    }
  }

  private startPolling(): void {
    if (this.pollHandle != null) return
    this.pollHandle = setInterval(() => {
      const t = this.safeCall(() => this.player?.getCurrentTime())
      if (t != null) {
        this.resample(t)
        this.events.onTimeUpdate(t)
      }
    }, POLL_INTERVAL_MS)
  }

  private stopPolling(): void {
    if (this.pollHandle != null) {
      clearInterval(this.pollHandle)
      this.pollHandle = null
    }
  }

  private resample(t: number): void {
    this.sample = { t, wall: performance.now() }
  }

  // Feature-guard every IFrame API call — the player can be torn down by
  // the browser (or not yet constructed) between our checks.
  private safeCall<T>(fn: () => T): T | undefined {
    try {
      return fn()
    } catch {
      return undefined
    }
  }

  play(): Promise<void> {
    if (this.player && this.ready) {
      this.safeCall(() => this.player!.playVideo())
    } else if (this.pending) {
      this.pending.autoplay = true
    }
    return Promise.resolve()
  }

  pause(): void {
    if (this.player && this.ready) {
      this.safeCall(() => this.player!.pauseVideo())
    } else if (this.pending) {
      this.pending.autoplay = false
    }
  }

  seekTo(seconds: number): void {
    if (!Number.isFinite(seconds)) return
    const target = Math.max(0, seconds)
    if (this.player && this.ready) {
      this.safeCall(() => this.player!.seekTo(target, true))
      this.resample(target)
      this.events.onSeeked()
    } else if (this.pending) {
      this.pending.seekTo = target
    }
  }

  setRate(rate: number): void {
    if (this.player && this.ready) {
      this.safeCall(() => this.player!.setPlaybackRate(rate))
    } else if (this.pending) {
      this.pending.rate = rate
    }
  }

  setVolume(volume: number): void {
    this.safeCall(() => this.player?.setVolume(Math.round(Math.min(1, Math.max(0, volume)) * 100)))
  }

  setMuted(muted: boolean): void {
    this.safeCall(() => (muted ? this.player?.mute() : this.player?.unMute()))
  }

  getCurrentTime(): number {
    if (!this.playing) return this.sample.t
    return this.sample.t + ((performance.now() - this.sample.wall) / 1000) * this.rate
  }

  getDuration(): number {
    const duration = this.safeCall(() => this.player?.getDuration())
    return duration && Number.isFinite(duration) ? duration : 0
  }

  isPlaying(): boolean {
    return this.playing
  }

  getRate(): number {
    return this.rate
  }

  destroy(): void {
    this.destroyed = true
    this.stopPolling()
    this.safeCall(() => this.player?.destroy())
    this.safeCall(() => this.container.replaceChildren())
    this.player = null
    this.ready = false
    this.pending = null
    this.currentVideoId = null
    this.playing = false
  }
}
