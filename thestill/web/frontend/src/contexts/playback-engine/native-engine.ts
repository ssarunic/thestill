// Spec #62 §5 — NativeEngine: the stable <video> element behind the
// PlaybackEngine interface. A mechanical extraction of the transport +
// event logic that previously lived inline in PlayerContext; behavior is
// deliberately identical (the pre-extraction PlayerContext test suite is
// the parity check).
//
// Spec #113 — HLS sources play through hls.js on the same element wherever
// Media Source Extensions exist, natively on Apple WebKit (see needsHlsJs).
// hls.js only feeds the element; every transport call and media event
// below works unchanged on top of it.

import { canPlayHlsNatively, HLS_ERROR_EVENT, loadHls, needsHlsJs, type HlsInstance, type HlsModule } from './hls'
import { rejectCrossOrigin } from '../../drift/corsVerdict'
import type { TimelineMap } from './timeline-map'
import type { EngineEvents, EngineSource, LoadOptions, PlaybackEngine } from './types'

// Numeric codes rather than the MediaError constants — the global is not
// guaranteed in non-browser environments (jsdom).
function describeMediaError(code: number | undefined): string {
  switch (code) {
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

/** Said when a source never loaded at all: refused, unreachable, or silent. */
export const AUDIO_NOT_LOADED_MESSAGE = "Couldn't load the audio. Try again in a moment."

// A late resume seek only moves a source that has barely started.
const LATE_SEEK_WINDOW_SECONDS = 5

// Spec #116 — the longest a transcript-time start waits in silence for its
// timeline map. Past this the listener hears the uncorrected position: a
// late correction is better than a player that seems stuck.
export const TIMELINE_HOLD_MAX_MS = 10_000

// hls.js's ErrorTypes values (stable strings) for the two fatal kinds the
// engine treats differently.
const HLS_NETWORK_ERROR = 'networkError'
const HLS_MEDIA_ERROR = 'mediaError'

// What a replaced engine on the same element hands to its successor: the
// element keeps its source across the swap, the engine's own state would not.
export interface NativeEngineState {
  url: string | null
  elementUrl?: string | null
  pendingSeek: number | null
  userSeeked: boolean
  // Spec #113 — the hls.js instance feeding the element, or true when an
  // attach of `url` is still waiting for the hls.js chunk.
  hls?: HlsInstance | null
  hlsAttaching?: boolean
  // Spec #116 — the timeline map in force for `url`, if any.
  timeline?: AppliedTimeline | null
}

/**
 * Spec #116 §5 — a verified map between transcript time and the time of
 * the copy this element is playing, with the transcribed copy's duration.
 * While one is set, every time the engine takes or reports is transcript
 * time: seeks land on the words that were clicked, the clock and karaoke
 * follow the words being spoken, and a saved position means the same
 * sentence in any copy.
 */
export interface AppliedTimeline {
  map: TimelineMap
  durationS: number
}

interface HlsErrorData {
  fatal: boolean
  type: string
}

// Safari's false end: how close to its last seek the element must have
// ended, and how far short of the known duration.
const FALSE_END_SEEK_SLACK_S = 0.5
const FALSE_END_MIN_SHORTFALL_S = 2

export class NativeEngine implements PlaybackEngine {
  readonly kind = 'native' as const

  private el: HTMLVideoElement
  private events: EngineEvents
  // Deferred to loadedmetadata — browsers silently clamp seeks on
  // unloaded media.
  private pendingSeek: number | null = null
  // A seek the user made on this source. A late resume position (spec #90)
  // never overrides it.
  private userSeeked = false
  // Last URL assigned. Also the error-suppression guard: clearing src
  // during unload() fires a spurious error event in some browsers.
  private url: string | null = null
  // What the element loads for `url`, when it is not `url` itself.
  private elementUrl: string | null = null
  // Spec #116 — the URL currently loaded with `crossorigin`, and whether a
  // play was asked for on it (to resume after the fallback reload).
  private crossOriginUrl: string | null = null
  private wantsPlay = false
  // Spec #116 — set only for the source it was verified against; cleared
  // by every source change.
  private timeline: AppliedTimeline | null = null
  // Spec #116 — a transcript-time start waiting for its map: the time the
  // listener asked for, and the deadline. The element stays PAUSED for the
  // wait (see beginHold) while the app is told playback is under way.
  private hold: {
    transcriptTime: number
    timer: ReturnType<typeof setTimeout>
  } | null = null
  // A held start that was paused before its map arrived: still owed.
  private pendingTranscriptTime: number | null = null
  // Safari's false end (see isFalseEnd): the longest duration this source
  // has reported, where the last seek went, the seek already recovered
  // once, and whether the element has ended since it last played.
  private longestDuration = 0
  private lastSeekTarget: number | null = null
  private recoveredAt: number | null = null
  private hasEnded = false
  private unbind: () => void
  // Spec #113 — hls.js state. `hlsGeneration` invalidates an attach still
  // waiting for the chunk when the source changes underneath it.
  private hls: HlsInstance | null = null
  private hlsAttach: Promise<void> | null = null
  private hlsGeneration = 0
  // Token of the play() call waiting on an attach; pause() and any source
  // change bump it so the deferred play is dropped.
  private deferredPlay = 0
  // Set after a fatal hls.js error tore the attachment down: the source
  // and the position it reached. The next play() re-attaches there
  // (Codex #466); meanwhile it stands in for the reset element's clock.
  private hlsRetry: { url: string; position: number } | null = null
  private hlsRecovered = false
  private readonly onHlsError = (_event: unknown, data: HlsErrorData) => this.handleHlsError(data)

  constructor(el: HTMLVideoElement, events: EngineEvents, carried?: NativeEngineState | null) {
    this.el = el
    this.events = events
    if (carried) {
      this.url = carried.url
      this.elementUrl = carried.elementUrl ?? null
      this.pendingSeek = carried.pendingSeek
      this.userSeeked = carried.userSeeked
      this.timeline = carried.timeline ?? null
    }

    const listeners: Array<[string, EventListener]> = [
      ['play', () => this.events.onPlay()],
      [
        'pause',
        () => {
          // The hold's own pause is not the listener's: for them playback
          // has started and is loading.
          if (this.hold && this.wantsPlay) return
          // Safari pauses for a false end first; `ended` deals with it.
          if (this.isFalseEnd()) return
          this.events.onPause()
        },
      ],
      [
        'playing',
        () => {
          this.hasEnded = false
          this.events.onPlaying()
        },
      ],
      [
        'seeking',
        () => {
          this.lastSeekTarget = this.el.currentTime
        },
      ],
      ['waiting', () => this.events.onWaiting()],
      ['canplay', () => this.events.onCanPlay()],
      ['timeupdate', () => this.events.onTimeUpdate(this.getCurrentTime())],
      [
        'loadedmetadata',
        () => {
          if (this.pendingSeek != null) {
            // A deferred seek is a position in this audio (see
            // getElementTime), never a transcript time.
            this.el.currentTime = this.pendingSeek
            this.pendingSeek = null
          }
        },
      ],
      [
        'durationchange',
        () => {
          if (!Number.isFinite(this.el.duration)) return
          this.longestDuration = Math.max(this.longestDuration, this.el.duration)
          this.events.onDurationChange(this.getDuration())
        },
      ],
      ['ratechange', () => this.events.onRateChange(this.el.playbackRate)],
      ['seeked', () => this.events.onSeeked()],
      ['volumechange', () => this.events.onVolumeChange(this.el.volume, this.el.muted)],
      [
        'error',
        () => {
          if (this.url === null) return
          // Spec #116 — a source loaded with `crossorigin` fails outright
          // when its host does not allow CORS. Fall back once to a plain
          // load of the same source; the listener only loses the check.
          if (this.crossOriginUrl === this.url) {
            this.reloadWithoutCrossOrigin()
            return
          }
          // Before any of the file arrived, the code says little: a refused
          // or unreachable address is reported as an unsupported format.
          const neverLoaded = this.el.readyState === 0 && this.longestDuration === 0
          // A source that was only being preloaded (a session restored on
          // page load): nobody asked for audio yet, so nobody is told. The
          // next press on Play loads it afresh (see play()).
          if (neverLoaded && !this.wantsPlay) return
          this.events.onError(neverLoaded ? AUDIO_NOT_LOADED_MESSAGE : describeMediaError(this.el.error?.code))
        },
      ],
      [
        'ended',
        () => {
          if (this.isFalseEnd()) {
            this.recoverFromFalseEnd()
            return
          }
          this.hasEnded = true
          this.events.onEnded()
        },
      ],
    ]
    for (const [name, listener] of listeners) el.addEventListener(name, listener)
    this.unbind = () => {
      for (const [name, listener] of listeners) el.removeEventListener(name, listener)
    }

    // Take over the predecessor's hls.js feed (StrictMode remount).
    if (carried?.hls) {
      this.hls = carried.hls
      this.hls.on(HLS_ERROR_EVENT, this.onHlsError)
    } else if (carried?.hlsAttaching && carried.url) {
      this.attachHls(carried.url)
    }
  }

  // Assign a new source and (optionally) carry seek/rate across the
  // transition.
  load(source: EngineSource, opts?: LoadOptions): void {
    if (!source.url) return
    this.detachHls()
    // A hold survives a reload of the same source (the reload that makes it
    // checkable); any other load ends it.
    const sameSource = this.url === source.url
    if (this.hold && !(sameSource && opts?.holdForTimeline)) this.endHold(false)
    this.pendingSeek = opts?.seekTo ?? null
    this.userSeeked = false
    this.url = source.url
    this.elementUrl = source.elementUrl ?? null
    this.wantsPlay = false
    this.longestDuration = 0
    this.lastSeekTarget = null
    this.recoveredAt = null
    this.hasEnded = false
    // A map belongs to one verified load of one copy.
    this.timeline = null
    if (!(sameSource && opts?.holdForTimeline)) this.pendingTranscriptTime = null
    const useHls = needsHlsJs(this.el, source.url, source.mimeType)
    // Never for an HLS stream: no map can be made for one.
    if (opts?.holdForTimeline && opts.seekTo != null && !useHls) {
      if (this.hold) this.hold.transcriptTime = opts.seekTo
      else this.beginHold(opts.seekTo)
    } else if (this.hold) {
      this.endHold(false)
    }
    // The attribute must be in place before the source is assigned.
    if (source.crossOrigin && !useHls) {
      this.el.crossOrigin = 'anonymous'
      this.crossOriginUrl = source.url
    } else {
      this.el.removeAttribute('crossorigin')
      this.crossOriginUrl = null
    }
    if (useHls) {
      // Stop the outgoing source now: hls.js attaches once its chunk has
      // loaded, and the old file must not keep playing in the meantime.
      if (this.el.hasAttribute('src')) this.clearSrc()
      this.attachHls(source.url)
    } else {
      this.el.src = this.elementUrl ?? source.url
    }
    const rate = opts?.rate
    if (rate != null && Number.isFinite(rate) && rate > 0) {
      // Loading resets playbackRate to defaultPlaybackRate; set both so
      // the carried rate survives the source transition.
      this.el.defaultPlaybackRate = rate
      this.el.playbackRate = rate
    }
  }

  private reloadWithoutCrossOrigin(): void {
    const url = this.url
    if (url === null) return
    rejectCrossOrigin(url)
    this.crossOriginUrl = null
    // Without the attribute nothing can be verified, so no map stays in force.
    this.timeline = null
    // Keep where playback was meant to be: a deferred seek that never ran
    // (the failure comes before metadata), else the position reached.
    const position = this.pendingSeek ?? (this.el.currentTime > 0 ? this.el.currentTime : null)
    this.el.removeAttribute('crossorigin')
    this.pendingSeek = position
    this.el.src = this.elementUrl ?? url
    // A held start stays paused; it is started when the hold ends.
    if (this.wantsPlay && !this.hold) void this.el.play().catch(() => this.startRefused())
  }

  // A start the engine made by itself (after a reload) was refused: the
  // app must not go on showing playback that is not happening.
  private startRefused(): void {
    this.wantsPlay = false
    this.events.onPause()
  }

  /**
   * Safari sometimes ends an MP3 where it has just been put: it reports
   * the seek target as the duration and fires `ended`, after which every
   * seek is clamped to that point and Play restarts from 0. Seen on a seek
   * back from the real end of the file (2026-11-11). The marks: the
   * element ended on the spot of its last seek, well short of a duration
   * this same source reported earlier.
   */
  private isFalseEnd(): boolean {
    if (this.url === null || !this.el.ended || this.lastSeekTarget === null) return false
    const at = this.el.currentTime
    if (Math.abs(at - this.lastSeekTarget) > FALSE_END_SEEK_SLACK_S) return false
    if (this.longestDuration - at < FALSE_END_MIN_SHORTFALL_S) return false
    // Once per place: an end that comes back after a reload is believed.
    return this.recoveredAt === null || Math.abs(this.recoveredAt - at) > FALSE_END_SEEK_SLACK_S
  }

  // A fresh load of the same source gives the true duration back. The map
  // stays: every range of the new load is still checked against it.
  private recoverFromFalseEnd(): void {
    const url = this.url
    if (url === null) return
    const at = this.el.currentTime
    this.recoveredAt = at
    this.lastSeekTarget = null
    this.pendingSeek = at
    this.el.src = this.elementUrl ?? url
    if (this.wantsPlay) void this.el.play().catch(() => this.startRefused())
    else this.events.onPause()
  }

  // Detach the source (session stop). Keeps the element itself alive —
  // it is the one stable media node (spec #61 invariant 2).
  unload(): void {
    this.detachHls()
    this.elementUrl = null
    this.el.pause()
    this.clearSrc()
    this.el.removeAttribute('crossorigin')
    this.crossOriginUrl = null
    this.wantsPlay = false
    this.timeline = null
    this.endHold(false)
    this.pendingTranscriptTime = null
    this.url = null
    this.pendingSeek = null
    this.userSeeked = false
  }

  play(): Promise<void> {
    this.wantsPlay = true
    // A held start that was paused before its map arrived is held again on
    // resume: still silent, with a fresh cap on the wait. (A hold that was
    // released or timed out left nothing pending.)
    if (this.pendingTranscriptTime !== null && this.timeline === null && !this.hold && this.url !== null) {
      const asked = this.pendingTranscriptTime
      this.pendingTranscriptTime = null
      this.beginHold(asked)
    }
    if (this.hold) {
      // Nothing may be heard yet, but this call is the listener's gesture
      // and the real start comes later, outside it. Starting and at once
      // pausing here is what lets that later play() through in Safari.
      void this.el.play().catch(() => {})
      this.el.pause()
      this.events.onPlay()
      return Promise.resolve()
    }
    if (this.hlsRetry) {
      const { url, position } = this.hlsRetry
      this.hlsRetry = null
      this.pendingSeek = position
      this.attachHls(url)
    }
    // An hls.js attach still waiting for its chunk: play once it lands.
    // Chrome and Firefox keep the user activation across the await; Safari
    // never takes this path (native HLS).
    if (this.hlsAttach) {
      const token = ++this.deferredPlay
      return this.hlsAttach.then(() => (token === this.deferredPlay ? this.el.play() : undefined))
    }
    // A seek made on an ended element has not landed yet. Started now,
    // Safari either restarts from 0 or ends again on the spot; so start
    // once the seek is done.
    if (this.hasEnded && this.el.seeking) {
      const token = ++this.deferredPlay
      return new Promise<void>((resolve, reject) => {
        this.el.addEventListener(
          'seeked',
          () => {
            if (token !== this.deferredPlay) resolve()
            else this.el.play().then(resolve, reject)
          },
          { once: true },
        )
      })
    }
    // A source that failed to load stays failed: play() on it is refused
    // without another request. Load it afresh, so that "try again" does.
    if (this.el.error && this.url !== null && this.hls === null) {
      this.pendingSeek = this.pendingSeek ?? (this.el.currentTime > 0 ? this.el.currentTime : null)
      this.el.src = this.elementUrl ?? this.url
    }
    return this.el.play()
  }

  pause(): void {
    // Also cancels a play() still waiting on an hls.js attach — e.g.
    // playYouTube() pausing this engine must not have it start later.
    this.deferredPlay++
    this.wantsPlay = false
    // The asked-for time of a held start is kept, so a map that arrives
    // meanwhile still moves to it.
    const held = this.hold !== null
    this.endHold(false, true)
    this.el.pause()
    // A held element is already paused, so no `pause` event is coming.
    if (held) this.events.onPause()
  }

  /**
   * `holdForTimeline` (spec #116): `seconds` is a transcript time the
   * listener asked for and a map is expected but not yet in force. The
   * engine waits, paused, and the seek is redone on the map when it arrives.
   */
  seekTo(seconds: number, options?: { holdForTimeline?: boolean }): void {
    if (!Number.isFinite(seconds)) return
    if (this.hlsRetry) {
      this.hlsRetry.position = Math.max(0, seconds)
      return
    }
    this.userSeeked = true
    // An explicit seek supersedes a deferred one (a resume position queued
    // for loadedmetadata), or metadata would jump back to it.
    this.pendingSeek = null
    // Never for an HLS stream: no map can be made for one (setTimeline
    // refuses it), so a hold would only be ten seconds of silence.
    const canHold = this.url !== null && this.hls === null && this.hlsAttach === null
    if (this.timeline === null && options?.holdForTimeline && canHold) {
      if (this.hold) this.hold.transcriptTime = Math.max(0, seconds)
      else this.beginHold(Math.max(0, seconds))
      this.el.currentTime = Math.max(0, seconds)
      return
    }
    // Any other seek is the listener choosing a place themselves.
    const wasHeld = this.hold !== null
    this.endHold(false)
    this.pendingTranscriptTime = null
    this.el.currentTime = Math.max(0, this.toElementTime(seconds))
    if (wasHeld) this.startAfterHold()
  }

  // --- Spec #116: holding a transcript-time start for its map --------------

  /**
   * The wait is spent PAUSED, never muted. Safari cannot play an audio-only
   * file muted (or at volume 0): it ends the playback at once and reports
   * the position reached as the duration, after which every seek is clamped
   * and Play restarts from 0 (v1.42.0, 2026-11-11). A paused element has no
   * such trouble in any browser, and nothing runs on unheard.
   */
  private beginHold(transcriptTime: number): void {
    // One request at a time: a paused one (pendingTranscriptTime) is
    // replaced by whatever is held now.
    this.pendingTranscriptTime = null
    const timer = setTimeout(() => {
      this.endHold(true)
      this.startAfterHold()
    }, TIMELINE_HOLD_MAX_MS)
    this.hold = { transcriptTime, timer }
    // Already playing (a click while the check is still running): the app
    // goes on showing playback, so that is what resumes afterwards.
    if (!this.el.paused) {
      this.wantsPlay = true
      this.el.pause()
    }
    this.events.onTimelineHoldChange?.(true)
  }

  /**
   * End the wait. `restart` puts the element at the time that was asked
   * for, uncorrected: no map came. `keepRequest` leaves the asked-for time
   * in place for a map that may still arrive (a pause). Starting playback
   * is the caller's move (startAfterHold), once the element is in place.
   */
  private endHold(restart: boolean, keepRequest = false): void {
    const hold = this.hold
    if (!hold) return
    clearTimeout(hold.timer)
    this.hold = null
    if (keepRequest) this.pendingTranscriptTime = hold.transcriptTime
    if (restart && this.url !== null) {
      if (this.el.readyState < HTMLMediaElement.HAVE_METADATA) this.pendingSeek = hold.transcriptTime
      else this.el.currentTime = hold.transcriptTime
    }
    this.events.onTimelineHoldChange?.(false)
  }

  // The start the listener asked for, now that the element is in place.
  private startAfterHold(): void {
    if (!this.wantsPlay || this.url === null) return
    const url = this.url
    void this.el.play().catch(() => {
      // Refused (no gesture left to draw on): show it, so Play is one tap.
      if (this.url !== url || this.hold || !this.el.paused) return
      this.wantsPlay = false
      this.events.onPause()
    })
  }

  /** The hook's word that no map is coming for this source. */
  releaseTimelineHold(url: string): void {
    if (this.url !== url) return
    if (this.hold) {
      this.endHold(true)
      this.startAfterHold()
      return
    }
    // A held start that the listener paused: nothing is waiting, but the
    // request is still pending and would be held again on resume. Settle it
    // here, at the asked-for time, without starting playback.
    const asked = this.pendingTranscriptTime
    if (asked === null) return
    this.pendingTranscriptTime = null
    if (this.el.readyState < HTMLMediaElement.HAVE_METADATA) this.pendingSeek = asked
    else this.el.currentTime = asked
  }

  isHoldingForTimeline(): boolean {
    return this.hold !== null
  }

  /** The transcript time a held start is waiting on, if any. */
  heldTranscriptTime(): number | null {
    return this.hold?.transcriptTime ?? null
  }

  // Spec #116 — between the time the app speaks (transcript time while a
  // map is set) and the element's own clock.
  private toElementTime(seconds: number): number {
    return this.timeline ? this.timeline.map.toAsset(seconds).time : seconds
  }

  private fromElementTime(seconds: number): number {
    return this.timeline ? this.timeline.map.toLogicalHeld(seconds) : seconds
  }

  /**
   * Put a verified map in force for the current source, or lift it (null).
   * Returns false when `url` is no longer the loaded source. The audio does
   * not move: only the time the engine reports for it changes, so the app
   * is told at once.
   */
  setTimeline(url: string, timeline: AppliedTimeline | null): boolean {
    if (this.url !== url || this.hls !== null || this.hlsAttach !== null) return false
    if (this.timeline === timeline) return true
    this.timeline = timeline
    if (timeline !== null) {
      // A start that was waiting for this map: go to where the asked-for
      // words are in this copy, then start.
      const wasHeld = this.hold !== null
      const asked = this.hold?.transcriptTime ?? this.pendingTranscriptTime
      this.pendingTranscriptTime = null
      if (asked !== null && asked !== undefined) {
        const target = Math.max(0, timeline.map.toAsset(asked).time)
        if (this.el.readyState < HTMLMediaElement.HAVE_METADATA) this.pendingSeek = target
        else this.el.currentTime = target
      }
      this.endHold(false)
      if (wasHeld) this.startAfterHold()
    }
    this.events.onTimeUpdate(this.getCurrentTime())
    if (Number.isFinite(this.el.duration)) this.events.onDurationChange(this.getDuration())
    return true
  }

  hasTimeline(): boolean {
    return this.timeline !== null
  }

  /** Whether the element has audio to play right now (it is not waiting
   * on the network): a start accepted in this state is already under way. */
  canPlayNow(): boolean {
    return this.el.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA
  }

  /** Whether any of the source has arrived: metadata or more, or a clock
   * that has moved off zero. */
  hasLoadedAnything(): boolean {
    return this.el.readyState > 0 || this.el.currentTime > 0
  }

  // Spec #90 — apply a resume position learned after the source was
  // assigned (a parallel fetch for surfaces whose payload had none). Never
  // after a user seek on this source. Otherwise, before metadata it replaces
  // the deferred seek; afterwards it seeks only while the source is still
  // near its start.
  // Returns false when the position no longer applies.
  setPendingSeek(seconds: number): boolean {
    if (this.url === null || !Number.isFinite(seconds) || seconds < 0) return false
    // A seek the user made wins, metadata or not (a transcript click while
    // the source is still loading).
    if (this.userSeeked) return false
    if (this.el.readyState < HTMLMediaElement.HAVE_METADATA) {
      this.pendingSeek = seconds
      return true
    }
    if (this.el.currentTime >= LATE_SEEK_WINDOW_SECONDS) return false
    this.el.currentTime = seconds
    return true
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
    return this.hlsRetry ? this.hlsRetry.position : this.fromElementTime(this.el.currentTime)
  }

  /**
   * Spec #116 — where the audio itself is, whatever map is in force. This
   * is what a saved listening position holds, and what a resume seeks to
   * (`LoadOptions.seekTo`, `setPendingSeek`): on the next load the player
   * seeks before any map can exist, so a position saved as transcript time
   * would resume off by the ad shift even when the copy has not changed.
   */
  getElementTime(): number {
    if (this.hlsRetry) return this.hlsRetry.position
    // While a start is held (or paused before its map came), playback is,
    // for the listener, at the place they asked for, and that is what a
    // saved position must say even before the element has got there.
    const asked = this.hold?.transcriptTime ?? this.pendingTranscriptTime
    return asked ?? this.el.currentTime
  }

  /** The audio's own duration, whatever map is in force. */
  getElementDuration(): number {
    return Number.isFinite(this.el.duration) ? this.el.duration : 0
  }

  getDuration(): number {
    if (!Number.isFinite(this.el.duration)) return 0
    // With a map, the app's timeline is the transcribed copy's.
    return this.timeline ? this.timeline.durationS : this.el.duration
  }

  isPlaying(): boolean {
    // A held start is paused underneath, but under way for the listener.
    if (this.hold) return this.wantsPlay
    return !this.el.paused && !this.el.ended
  }

  getRate(): number {
    return this.el.playbackRate
  }

  /**
   * Unbind from the element. Returns the state a successor on the same
   * element needs — StrictMode's dev remount replaces the engine while a
   * deferred seek (a restore landing on loadedmetadata) is still pending.
   */
  destroy(): NativeEngineState {
    // The successor does not inherit a hold.
    this.endHold(false)
    this.unbind()
    const hls = this.hls
    const hlsAttaching = this.hlsAttach !== null
    hls?.off(HLS_ERROR_EVENT, this.onHlsError)
    // Hand over, don't tear down: the element keeps playing for the successor.
    this.hls = null
    this.hlsAttach = null
    this.hlsGeneration++
    this.deferredPlay++ // a destroyed engine never starts playback
    return {
      url: this.url,
      elementUrl: this.elementUrl,
      pendingSeek: this.pendingSeek,
      userSeeked: this.userSeeked,
      hls,
      hlsAttaching,
      timeline: this.timeline,
    }
  }

  private clearSrc(): void {
    this.el.removeAttribute('src')
    this.el.load()
  }

  // --- Spec #113: hls.js ---------------------------------------------------

  private attachHls(url: string): void {
    const generation = ++this.hlsGeneration
    this.hlsRecovered = false
    const attach = loadHls().then(
      (hlsModule) => {
        if (generation !== this.hlsGeneration) return
        this.startHls(hlsModule, url)
      },
      () => {
        if (generation !== this.hlsGeneration) return
        this.failHls('The video player could not be loaded.')
      },
    )
    this.hlsAttach = attach.finally(() => {
      if (generation === this.hlsGeneration) this.hlsAttach = null
    })
  }

  private startHls({ Hls, workerPath }: HlsModule, url: string): void {
    if (!Hls.isSupported()) {
      // MSE exists but cannot play this (codecs): native HLS, if any, is
      // the better bet than an error.
      if (canPlayHlsNatively(this.el)) this.el.src = url
      else this.events.onError(describeMediaError(4)) // MEDIA_ERR_SRC_NOT_SUPPORTED
      return
    }
    const hls = new Hls({
      workerPath,
      // Fetch no more resolution than the slot shows; a hidden video
      // (zero-size media layer) drops to the lowest rung.
      capLevelToPlayerSize: true,
      // Start fetching where the deferred seek will land, not at 0.
      startPosition: this.pendingSeek ?? -1,
    })
    this.hls = hls
    hls.on(HLS_ERROR_EVENT, this.onHlsError)
    hls.attachMedia(this.el)
    hls.loadSource(url)
  }

  private handleHlsError(data: HlsErrorData): void {
    const hls = this.hls
    if (!data.fatal || !hls) return
    if (data.type === HLS_MEDIA_ERROR && !this.hlsRecovered) {
      this.hlsRecovered = true
      hls.recoverMediaError()
      return
    }
    // MEDIA_ERR_NETWORK / MEDIA_ERR_DECODE wording.
    this.failHls(describeMediaError(data.type === HLS_NETWORK_ERROR ? 2 : 3))
  }

  // Tear down after a failure (chunk download or fatal hls.js error) but
  // keep the source retryable: the next play() re-attaches at the position
  // reached, or the resume position not yet applied (failure before
  // loadedmetadata, element clock still 0). Codex #466.
  private failHls(message: string): void {
    const position = this.pendingSeek ?? this.el.currentTime
    this.detachHls()
    if (this.url !== null) this.hlsRetry = { url: this.url, position }
    this.events.onError(message)
  }

  // Tear down hls.js (and invalidate a pending attach). Leaves `url`
  // alone: callers decide what the element plays next.
  private detachHls(): void {
    this.hlsGeneration++
    this.deferredPlay++
    this.hlsRetry = null
    this.hlsAttach = null
    const hls = this.hls
    this.hls = null
    hls?.destroy()
  }
}
