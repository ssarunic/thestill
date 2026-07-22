// Spec #62 §5 — NativeEngine: the stable <video> element behind the
// PlaybackEngine interface. A mechanical extraction of the transport +
// event logic that previously lived inline in PlayerContext; behavior is
// deliberately identical (the pre-extraction PlayerContext test suite is
// the parity check).

import type { EngineEvents, EngineSource, LoadOptions, PlaybackEngine } from './types'

// Numeric codes rather than the MediaError constants — the global is not
// guaranteed in non-browser environments (jsdom).
function describeMediaError(error: MediaError | null): string {
  switch (error?.code) {
    case 1: // MEDIA_ERR_ABORTED
      return 'Playback was aborted.'
    case 2: // MEDIA_ERR_NETWORK
      return 'A network error interrupted playback.'
    case 3: // MEDIA_ERR_DECODE
      return 'The media could not be decoded.'
    case 4: // MEDIA_ERR_SRC_NOT_SUPPORTED
      return 'This media format is not supported by your browser.'
    default:
      return 'Playback failed.'
  }
}

export class NativeEngine implements PlaybackEngine {
  readonly kind = 'native' as const

  private el: HTMLVideoElement
  private events: EngineEvents
  // Deferred to loadedmetadata — browsers silently clamp seeks on
  // unloaded media.
  private pendingSeek: number | null = null
  // Last URL assigned. Also the error-suppression guard: clearing src
  // during unload() fires a spurious error event in some browsers.
  private url: string | null = null
  private unbind: () => void

  constructor(el: HTMLVideoElement, events: EngineEvents) {
    this.el = el
    this.events = events

    const listeners: Array<[string, EventListener]> = [
      ['play', () => this.events.onPlay()],
      ['pause', () => this.events.onPause()],
      ['playing', () => this.events.onPlaying()],
      ['waiting', () => this.events.onWaiting()],
      ['canplay', () => this.events.onCanPlay()],
      ['timeupdate', () => this.events.onTimeUpdate(this.el.currentTime)],
      [
        'loadedmetadata',
        () => {
          if (this.pendingSeek != null) {
            this.el.currentTime = this.pendingSeek
            this.pendingSeek = null
          }
        },
      ],
      [
        'durationchange',
        () => {
          if (Number.isFinite(this.el.duration)) this.events.onDurationChange(this.el.duration)
        },
      ],
      ['ratechange', () => this.events.onRateChange(this.el.playbackRate)],
      ['seeked', () => this.events.onSeeked()],
      ['volumechange', () => this.events.onVolumeChange(this.el.volume, this.el.muted)],
      [
        'error',
        () => {
          if (this.url !== null) this.events.onError(describeMediaError(this.el.error))
        },
      ],
      ['ended', () => this.events.onEnded()],
    ]
    for (const [name, listener] of listeners) el.addEventListener(name, listener)
    this.unbind = () => {
      for (const [name, listener] of listeners) el.removeEventListener(name, listener)
    }
  }

  // Assign a new source and (optionally) carry seek/rate across the
  // transition.
  load(source: EngineSource, opts?: LoadOptions): void {
    if (!source.url) return
    this.el.src = source.url
    this.url = source.url
    this.pendingSeek = opts?.seekTo ?? null
    const rate = opts?.rate
    if (rate != null && Number.isFinite(rate) && rate > 0) {
      // Loading resets playbackRate to defaultPlaybackRate; set both so
      // the carried rate survives the source transition.
      this.el.defaultPlaybackRate = rate
      this.el.playbackRate = rate
    }
  }

  // Detach the source (session stop). Keeps the element itself alive —
  // it is the one stable media node (spec #61 invariant 2).
  unload(): void {
    this.el.pause()
    this.el.removeAttribute('src')
    this.el.load()
    this.url = null
    this.pendingSeek = null
  }

  play(): Promise<void> {
    return this.el.play()
  }

  pause(): void {
    this.el.pause()
  }

  seekTo(seconds: number): void {
    if (Number.isFinite(seconds)) this.el.currentTime = Math.max(0, seconds)
  }

  setRate(rate: number): void {
    this.el.defaultPlaybackRate = rate
    this.el.playbackRate = rate
  }

  setVolume(volume: number): void {
    this.el.volume = Math.min(1, Math.max(0, volume))
    if (this.el.volume > 0 && this.el.muted) this.el.muted = false
  }

  setMuted(muted: boolean): void {
    this.el.muted = muted
  }

  getCurrentTime(): number {
    return this.el.currentTime
  }

  getDuration(): number {
    return Number.isFinite(this.el.duration) ? this.el.duration : 0
  }

  isPlaying(): boolean {
    return !this.el.paused && !this.el.ended
  }

  getRate(): number {
    return this.el.playbackRate
  }

  destroy(): void {
    this.unbind()
  }
}
