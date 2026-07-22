// Spec #62 §5 — the engine adapter boundary spec #61 invariant 1 promised.
//
// A PlaybackEngine is a thin imperative wrapper around one concrete player
// (the stable <video> element, or the YouTube IFrame API player). It holds
// NO React state: PlayerContext is the only thing that turns EngineEvents
// callbacks into useState updates, exactly as it did for the <video> DOM
// events before this abstraction existed. Exactly one engine is active at
// a time; both engines' stable DOM nodes coexist inside the global media
// layer with at most one visible.

export type EngineKind = 'native' | 'youtube'

// Everything PlayerContext needs to hear from an engine. Native maps DOM
// media events 1:1; YouTube maps IFrame API state changes plus a ~250 ms
// polled clock (onTimeUpdate cadence).
export interface EngineEvents {
  onPlay: () => void
  onPause: () => void
  onPlaying: () => void
  onWaiting: () => void
  onCanPlay: () => void
  onTimeUpdate: (seconds: number) => void
  onDurationChange: (seconds: number) => void
  onRateChange: (rate: number) => void
  onSeeked: () => void
  onVolumeChange: (volume: number, muted: boolean) => void
  onError: (message: string) => void
  onEnded: () => void
}

// Native: `url` (the enclosure/asset URL). YouTube: `videoId`.
export interface EngineSource {
  url?: string
  videoId?: string
}

export interface LoadOptions {
  // Seek applied when the engine can honor it (native defers to
  // loadedmetadata — browsers silently clamp seeks on unloaded media;
  // YouTube applies it on player ready).
  seekTo?: number | null
  // Carry a playback rate across the source transition.
  rate?: number | null
  // Request playback as soon as the source is ready (YouTube constructs
  // its player asynchronously, so the play intent must ride along).
  autoplay?: boolean
}

export interface PlaybackEngine {
  readonly kind: EngineKind
  load(source: EngineSource, opts?: LoadOptions): void
  play(): Promise<void>
  pause(): void
  seekTo(seconds: number): void
  setRate(rate: number): void
  setVolume(volume: number): void
  setMuted(muted: boolean): void
  getCurrentTime(): number
  getDuration(): number
  isPlaying(): boolean
  getRate(): number
  destroy(): void
}
