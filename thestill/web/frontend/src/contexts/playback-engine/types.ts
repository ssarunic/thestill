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
  // Spec #72 §5 — the rates this engine accepts for the current source, or
  // null for "no restriction" (native media plays any sane rate). Only the
  // YouTube engine emits; it re-emits per video since the list can change.
  onAvailableRatesChange: (rates: number[] | null) => void
  // Spec #116 — native only: the engine is silently waiting for a timeline
  // map before letting a transcript-time start be heard (see LoadOptions).
  onTimelineHoldChange?: (holding: boolean) => void
}

// Native: `url` (the enclosure/asset URL) plus its manifest `mimeType`,
// which routes HLS through hls.js where the browser lacks native HLS
// (spec #113). YouTube: `videoId`.
export interface EngineSource {
  url?: string
  mimeType?: string | null
  videoId?: string
  // Spec #116 — native only: load with `crossorigin="anonymous"` so a
  // service worker can read what the element is served. Only ever set for
  // a host known to allow it; the engine reloads without it on failure.
  crossOrigin?: boolean
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
  // Spec #116 — native only. `seekTo` is a transcript time the listener
  // asked for (a click on a paragraph), and a timeline map is expected for
  // this source. The engine then plays muted until the map is in force,
  // moves to the corrected position and only then lets the audio be heard,
  // so the click never plays the wrong audio. If no map arrives it starts
  // at `seekTo` as it would have without this option.
  holdForTimeline?: boolean
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
