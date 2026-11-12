import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type { PlaybackManifest } from '../api/types'
import {
  setMediaSessionActionHandlers,
  setMediaSessionMetadata,
  setMediaSessionPlaybackState,
  updateMediaSessionPositionState,
} from '../utils/mediaSession'
import { usePlayerRatePreference } from '../hooks/usePlayerRatePreference'
import { mediaLayerZIndex } from '../constants/layers'
import { usePlaybackPersistence, type PlaybackPersistence, type PlayerPort } from './listening/usePlaybackPersistence'
import {
  AUDIO_NOT_LOADED_MESSAGE,
  NativeEngine,
  type AppliedTimeline,
  type NativeEngineState,
} from './playback-engine/native-engine'
import { elementAudioUrl } from '../utils/elementAudioUrl'
import type { ListenerAd, TimelineMap } from './playback-engine/timeline-map'
import type { EngineEvents, EngineKind } from './playback-engine/types'
import { beginAudioLoad } from '../drift/corsVerdict'
import { isTimelineExpected } from '../drift/expectation'
import { YouTubeEngine } from './playback-engine/youtube-engine'

// ---------------------------------------------------------------------------
// Spec #61 — unified audio/video playback session.
//
// One logical playback session, exactly one active playback engine. The
// native engine wraps a single stable <video> element (HTMLMediaElement
// plays audio resources fine) created once inside a global media layer and
// NEVER reparented — React portals with changing targets remount their
// children, and moving a <video> in the DOM pauses playback per the HTML
// spec. The YouTube engine (spec #62) wraps the IFrame Player API around a
// second stable node in the same layer; at most one node is visible, and
// each engine's events are ignored while the other is active. Presentation
// surfaces (theater slot in the episode reader, the floating tile) register
// a rectangle; the media layer positions the layer over the active slot.
// Presentation is modeled separately from playback: which surface shows the
// video is a UI state machine; play/pause/position/rate belong to the
// session and are never affected by surface changes.
// ---------------------------------------------------------------------------

export type MediaKind = 'audio' | 'video'
type Presentation = 'hidden' | 'theater' | 'floating' | 'native-pip'
// How long a press on Play may go without any of the audio arriving
// before the player says it could not be loaded.
export const LOAD_PATIENCE_MS = 15_000

export type VideoPreference = 'shown' | 'audio-only'
export type RenditionKind = 'audio' | 'video'
export type { EngineKind } from './playback-engine/types'

export interface PlayerTrack {
  episodeId: string
  podcastSlug: string
  episodeSlug: string
  title: string
  podcastTitle?: string | null
  audioUrl: string
  artworkUrl?: string | null
  durationHint?: number | null
  // Spec #61 §4 — playback-asset manifest. Optional: tracks started from
  // surfaces that only know audio_url (search, ⌘K, entity pages) keep
  // working as plain audio.
  playback?: PlaybackManifest | null
}

export interface PlayOptions {
  // Start playback at this position in seconds ON THE ASSET'S TIMELINE
  // (engine time), the same convention as seek(): callers already fold in
  // the per-episode playback offset (transcript segments and summary
  // citations both emit `segment.start + playback_time_offset_seconds`),
  // so play() assigns it to the engine verbatim — adding the asset's
  // timelineOffset here would double-apply it. For a new track the seek is
  // deferred until the media element reports metadata (duration) is
  // available; browsers silently clamp seeks on unloaded media.
  startAt?: number
  // Spec #90 "Play from beginning" — ignore the stored listening position
  // and start at the top of the asset. Without startAt/restart, a fresh
  // play of a part-listened episode resumes where it stopped.
  restart?: boolean
}

export interface PlayerContextValue {
  track: PlayerTrack | null
  isPlaying: boolean
  isLoading: boolean
  duration: number
  // Spec #113 — the duration the native engine reported for its current
  // source (the rendition actually loaded), or null until it reports.
  // Unlike `duration` it never holds the feed's durationHint, so the
  // reader's drift check can compare the transcript against real media.
  nativeDuration: number | null
  playbackRate: number
  // Spec #72 §5 — rates the active engine accepts for the current source;
  // null = no restriction (native) or not reported yet. Consumers disable
  // chips outside the list. The persisted preference itself is applied by
  // the provider on every new source, so no consumer needs to re-apply it.
  availableRates: number[] | null
  play: (track: PlayerTrack, options?: PlayOptions) => void
  pause: () => void
  resume: () => void
  toggle: () => void
  // `transcript` (spec #116): the time is a transcript time the listener
  // picked (a paragraph, a citation, a chapter). If a timeline map is
  // expected for this audio but not yet in force, the player goes silent
  // and lands on the corrected position once the map arrives.
  seek: (seconds: number, options?: { transcript?: boolean }) => void
  skip: (deltaSeconds: number) => void
  setRate: (rate: number) => void
  stop: () => void
  // Spec #90 — manual Mark as played / unplayed. Marking the current track
  // played pauses it and rewinds the bar to the top.
  markListened: (episodeId: string, played: boolean) => Promise<void>
  isCurrent: (episodeId: string) => boolean
  // Spec #38 — stable getter so a rAF loop can read the media element's
  // ``currentTime`` at 60 fps without subscribing to ``usePlayerTime``
  // (which only ticks at the browser's ~4 Hz ``timeupdate`` cadence).
  // Returns 0 before the media element mounts. Reports ENGINE time on the
  // active asset's timeline — the karaoke word payload's per-episode offset
  // already maps transcript time onto this timeline, so consumers stay
  // rendition-agnostic (spec #61 §7): rendition switches re-seek the engine
  // so `engineTime - activeOffset` (logical time) is preserved.
  getCurrentTime: () => number

  // --- Spec #61: presentation state machine + renditions -----------------
  // 'video' when the current track's manifest carries a video rendition.
  mediaKind: MediaKind
  // Which surface currently shows the video (§3). 'hidden' = audio-first.
  presentation: Presentation
  // Visual-off toggle (§5 "Hide video"): same resource, instant, no
  // continuity break. Distinct from switching to the audio rendition.
  videoPreference: VideoPreference
  setVideoPreference: (pref: VideoPreference) => void
  // Which rendition the engine is playing right now.
  activeRendition: RenditionKind
  // True when the manifest carries BOTH renditions (§5 "Use audio
  // rendition" is only offered when there is one to switch to).
  canSwitchRendition: boolean
  // Controlled source transition preserving logical position, rate and
  // play state, adjusted by per-asset timelineOffset (§4, §7).
  switchRendition: (target: RenditionKind) => void
  // --- Spec #62: YouTube iframe engine -----------------------------------
  // Which engine is rendering right now. 'youtube' only ever by explicit
  // user opt-in (playYouTube); every path that would leave the iframe
  // unpresented switches back to the native audio rendition (spec #62 §7).
  activeEngine: EngineKind
  // True when the current track's manifest carries an episode-level
  // YouTube link — gates the "Watch video" affordances.
  youtubeAvailable: boolean
  // User-gesture entry into the YouTube rendition for a track. Position is
  // carried best-effort: the YouTube timeline has no offset mapping
  // (dynamic ad insertion, spec #62 §8).
  playYouTube: (track: PlayerTrack) => void
  // Surfaces register a rectangle; the media layer positions the stable
  // video node over the active slot (§3 — mounted surface registration,
  // not pathname). Returns an unregister function.
  registerTheaterSlot: (episodeId: string, el: HTMLElement) => () => void
  registerFloatingSlot: (el: HTMLElement) => () => void
  // Spec #72 2c — synchronous "is a theater slot registered right now?" for
  // a would-be second host (the phone Now Playing sheet) to yield to the
  // reader's slot instead of stealing it. A getter, not state: reactive
  // state would re-run the sheet's registration effect on its own
  // registration and oscillate.
  hasTheaterSlot: () => boolean
  // Native PiP — user-initiated, progressive enhancement only (§2).
  // Browser state is authoritative: pipActive follows the
  // enter/leavepictureinpicture events, never assumptions.
  pipSupported: boolean
  pipActive: boolean
  requestPip: () => void
  // Spec #116 — put a verified timeline map in force for the audio now
  // playing (or lift it with null). While one is set, every time this
  // context takes or reports for that source is transcript time: `seek`
  // lands on the words asked for, `usePlayerTime`/`getCurrentTime` follow
  // the words being spoken, and saved positions mean the same sentence in
  // any copy. Refused (false) unless `episodeId` and `audioUrl` are what
  // the native engine is playing as the audio rendition.
  applyTimeline: (episodeId: string, audioUrl: string, timeline: AppliedTimeline | null) => boolean
  // True while a map is in force. Any change of source lifts it.
  timelineApplied: boolean
  // Spec #116 §6.1 — the map in force for the audio the native engine is
  // playing (null without one, and on the YouTube engine): the reader asks
  // it what became of each ad break in this copy.
  timelineMap: TimelineMap | null
  // The ad of the listener's own copy that is playing now, if any. The
  // transcript has no words for it, so the clock stands still meanwhile.
  // Changes only when an ad starts or ends.
  listenerAd: ListenerAd | null
  // Where the audio element itself is (seconds on the listener's copy),
  // whatever map is in force. Stable; for counting down a `listenerAd`.
  getAssetTime: () => number
  // How long the listener's copy itself is, or null until the element
  // says. Stable. Not `duration` / `nativeDuration`: while a map is in
  // force those are the transcribed copy's length.
  getAssetDuration: () => number | null
  // True while a transcript-time start is being held, silent, for its map.
  timelineHold: boolean
  // The reader's word that no map is coming for the held start: play from
  // the asked-for time as it is.
  releaseTimelineHold: (episodeId: string, audioUrl: string) => void
  // Reload the held audio so that it can be checked (with `crossorigin`,
  // now that its host is known to allow it, and under the service worker).
  // The listener hears nothing of it: the start is still held. False when
  // there is no held start for this audio.
  reloadHeldForCheck: (episodeId: string, audioUrl: string) => boolean
  // Media error surface (§8). Cleared on the next successful source.
  mediaError: string | null
  // Volume / mute (§8).
  volume: number
  muted: boolean
  setVolume: (volume: number) => void
  toggleMute: () => void
}

const PlayerContext = createContext<PlayerContextValue | null>(null)
const PlayerTimeContext = createContext<number>(0)

interface ActiveSource {
  url: string
  // The asset's manifest MIME type — routes HLS through hls.js where the
  // browser has no native HLS (spec #113). Null for the legacy audioUrl.
  mimeType: string | null
  offset: number
  rendition: RenditionKind
}

// 'video' only when the manifest both declares video AND carries the asset.
function trackMediaKind(track: PlayerTrack | null): MediaKind {
  return track?.playback?.kind === 'video' && track.playback.video ? 'video' : 'audio'
}

// Pick the engine source for a rendition preference, falling back to the
// other rendition's asset, then to the legacy audioUrl.
function selectSource(track: PlayerTrack, preferred: RenditionKind): ActiveSource {
  const manifest = track.playback
  const pick = (rendition: RenditionKind): ActiveSource | null => {
    const asset = rendition === 'video' ? manifest?.video : manifest?.audio
    return asset
      ? { url: asset.url, mimeType: asset.mime_type ?? null, offset: asset.timeline_offset ?? 0, rendition }
      : null
  }
  return (
    pick(preferred) ??
    pick(preferred === 'video' ? 'audio' : 'video') ?? {
      url: track.audioUrl,
      mimeType: null,
      offset: 0,
      rendition: 'audio',
    }
  )
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const mediaRef = useRef<HTMLVideoElement | null>(null)
  const layerRef = useRef<HTMLDivElement | null>(null)
  // Stable container for the YouTube iframe (spec #62) — always mounted
  // next to the <video> inside the media layer; the IFrame API only
  // touches it once YouTube playback is first requested.
  const youtubeContainerRef = useRef<HTMLDivElement | null>(null)
  const trackRef = useRef<PlayerTrack | null>(null)
  // Last URL assigned to the native engine. Compared on same-episode
  // play() calls so a rendition/manifest change becomes a controlled
  // source transition instead of silently resuming the old source (§5).
  const srcRef = useRef<string | null>(null)
  const renditionRef = useRef<RenditionKind>('audio')
  // timeline_offset of the asset currently in the NATIVE engine. Logical
  // time = engineTime - activeOffset; transitions re-seek so it is
  // preserved. The YouTube timeline has no offset (spec #62 §8).
  const activeOffsetRef = useRef(0)
  // Engine dispatch (spec #62 §5): exactly one engine is active. The ref
  // is the synchronous source of truth (transport calls, event guards);
  // the state mirrors it for rendering.
  const engineKindRef = useRef<EngineKind>('native')
  const nativeEngineRef = useRef<NativeEngine | null>(null)
  const youtubeEngineRef = useRef<YouTubeEngine | null>(null)

  const [track, setTrack] = useState<PlayerTrack | null>(null)
  const [activeEngine, setActiveEngine] = useState<EngineKind>('native')
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [nativeDuration, setNativeDuration] = useState<number | null>(null)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [availableRates, setAvailableRates] = useState<number[] | null>(null)
  // Spec #72 §5 — the one global rate preference, read synchronously inside
  // play()/playYouTube() so a fresh source starts at the user's speed.
  const { preferredRateRef, persistRate } = usePlayerRatePreference()
  const [activeRendition, setActiveRendition] = useState<RenditionKind>('audio')
  const [videoPreference, setVideoPreference] = useState<VideoPreference>('shown')
  const [pipActive, setPipActive] = useState(false)
  const [mediaError, setMediaError] = useState<string | null>(null)
  // A press on Play is answered at once (the button shows it is loading)
  // and never left hanging: if nothing of the audio has arrived after
  // LOAD_PATIENCE_MS, the player says so. A browser that refuses a source
  // outright does not always fire `error`.
  const loadWatch = useRef<number | undefined>(undefined)
  const stopLoadWatch = useCallback(() => {
    window.clearTimeout(loadWatch.current)
    loadWatch.current = undefined
  }, [])
  // Each press is one attempt. Its start can be refused long after another
  // attempt has replaced it (assigning a new source aborts the old play()),
  // and that late refusal must not touch the newer attempt's state.
  const loadAttempt = useRef(0)
  const armLoadWatch = useCallback(() => {
    window.clearTimeout(loadWatch.current)
    loadWatch.current = window.setTimeout(() => {
      loadWatch.current = undefined
      const native = nativeEngineRef.current
      if (engineKindRef.current !== 'native' || !native || native.hasLoadedAnything()) return
      // Withdraw the pending start: the audio must not begin later without
      // a press, and the button, now Play again, must play when pressed.
      native.pause()
      setMediaError(AUDIO_NOT_LOADED_MESSAGE)
      setIsLoading(false)
      setIsPlaying(false)
    }, LOAD_PATIENCE_MS)
  }, [])
  /**
   * Begin waiting for audio, and follow the start to its outcome. `started`
   * is the engine's play() promise.
   * - Refused (no gesture, or the start was superseded): the wait ends,
   *   unless a newer attempt has begun.
   * - Accepted with audio already in hand (the source was playing, or is
   *   buffered): the wait ends too. No `playing` or `canplay` follows a
   *   play() on audio that is already flowing, so nothing else would end it.
   * - Accepted with audio still to come: `playing`, an error or the timer
   *   ends it.
   */
  const awaitAudio = useCallback(
    (started: Promise<void> | undefined) => {
      const attempt = ++loadAttempt.current
      setIsLoading(true)
      // A new attempt: whatever the last one ended on is no longer the news.
      setMediaError(null)
      armLoadWatch()
      const settle = (accepted: boolean) => {
        if (loadAttempt.current !== attempt) return
        if (accepted) {
          // The YouTube player reports its own buffering and playing.
          const flowing = engineKindRef.current !== 'native' || nativeEngineRef.current?.canPlayNow() === true
          if (!flowing) return
        } else {
          setIsPlaying(false)
        }
        setIsLoading(false)
        stopLoadWatch()
      }
      started?.then(
        () => settle(true),
        () => settle(false),
      )
    },
    [stopLoadWatch, armLoadWatch],
  )
  useEffect(() => stopLoadWatch, [stopLoadWatch])
  const [timelineApplied, setTimelineApplied] = useState(false)
  // Spec #116 §6.1 — the map itself, for the reader's ad rows, and the ad
  // of the listener's own that is playing. The ref serves the time handler.
  const [timelineMap, setTimelineMapState] = useState<TimelineMap | null>(null)
  const timelineMapRef = useRef<TimelineMap | null>(null)
  const [listenerAd, setListenerAd] = useState<ListenerAd | null>(null)
  const setTimelineMap = useCallback((map: TimelineMap | null) => {
    timelineMapRef.current = map
    setTimelineMapState(map)
    if (map === null) setListenerAd(null)
  }, [])
  const [timelineHold, setTimelineHold] = useState(false)
  const [volume, setVolumeState] = useState(1)
  const [muted, setMuted] = useState(false)
  const [theaterSlot, setTheaterSlot] = useState<{ episodeId: string; el: HTMLElement } | null>(null)
  const [floatingSlot, setFloatingSlot] = useState<HTMLElement | null>(null)
  // Synchronous mirrors of the slots for the §7 compliance effect: slot
  // registration happens in CHILD effects (which run before this
  // provider's effects in the same commit), but the state they set lags a
  // commit behind — the refs let the effect see "a surface registered
  // this very commit" and not bounce a fresh YouTube session to audio.
  const theaterSlotRef = useRef<{ episodeId: string; el: HTMLElement } | null>(null)
  const floatingSlotRef = useRef<HTMLElement | null>(null)

  const setRendition = useCallback((rendition: RenditionKind) => {
    renditionRef.current = rendition
    setActiveRendition(rendition)
  }, [])

  // Same ref-is-sync-truth/state-mirrors discipline as setRendition, for
  // the engine axis (spec #62).
  const setEngineKind = useCallback((kind: EngineKind) => {
    engineKindRef.current = kind
    setActiveEngine(kind)
  }, [])

  // Spec #90 — listening progress persistence. Engine event bodies are
  // created before the hook runs (the hook needs the engines), so they
  // reach it through this ref.
  const persistenceRef = useRef<PlaybackPersistence | null>(null)

  const syncPositionState = useCallback(() => {
    const engine = engineKindRef.current === 'youtube' ? youtubeEngineRef.current : nativeEngineRef.current
    if (!engine) return
    updateMediaSessionPositionState(engine.getDuration(), engine.getRate(), engine.getCurrentTime())
  }, [])

  // One shared set of engine-event handler bodies — the exact logic the
  // <video> JSX handlers held before the engine extraction. Each engine
  // gets a guarded copy (below) so a late event from the inactive engine
  // (e.g. the iframe's async PAUSED arriving after a switch back to
  // native) can never clobber the active engine's state.
  const engineEventBodies = useMemo<EngineEvents>(
    () => ({
      onPlay: () => {
        setIsPlaying(true)
        syncPositionState()
        persistenceRef.current?.onEngineEvent('play')
      },
      onPause: () => {
        setIsPlaying(false)
        persistenceRef.current?.onEngineEvent('pause')
      },
      onPlaying: () => {
        setIsPlaying(true)
        setIsLoading(false)
        stopLoadWatch()
        // Playback is flowing again, so any earlier error has been
        // recovered from (spec #113: hls.js recoverMediaError after the
        // element already reported MEDIA_ERR_DECODE).
        setMediaError(null)
      },
      onWaiting: () => setIsLoading(true),
      onCanPlay: () => {
        setIsLoading(false)
        stopLoadWatch()
      },
      onTimeUpdate: (seconds: number) => {
        setCurrentTime(seconds)
        // The clock is moving: audio has arrived, whatever else is known.
        if (seconds > 0 && loadWatch.current !== undefined) stopLoadWatch()
        const map = timelineMapRef.current
        if (map !== null) {
          const ad =
            engineKindRef.current === 'native' ? map.listenerAdAt(nativeEngineRef.current?.getElementTime() ?? 0) : null
          // The same ad on every tick while it plays: keep the identity.
          setListenerAd((prev) => (prev?.startS === ad?.startS && prev?.endS === ad?.endS ? prev : ad))
        }
        persistenceRef.current?.onEngineEvent('timeupdate')
      },
      onDurationChange: (seconds: number) => {
        setDuration(seconds)
        syncPositionState()
      },
      onRateChange: (rate: number) => {
        setPlaybackRate(rate)
        syncPositionState()
      },
      onSeeked: () => {
        syncPositionState()
        persistenceRef.current?.onEngineEvent('seeked')
      },
      onVolumeChange: (volume: number, muted: boolean) => {
        setVolumeState(volume)
        setMuted(muted)
      },
      onError: (message: string) => {
        setMediaError(message)
        setIsLoading(false)
        setIsPlaying(false)
        stopLoadWatch()
      },
      onEnded: () => {
        persistenceRef.current?.onEngineEvent('ended')
        setIsPlaying(false)
        setCurrentTime(0)
        // The audio may end inside a post-roll of the listener's own.
        setListenerAd(null)
      },
      onAvailableRatesChange: (rates: number[] | null) => setAvailableRates(rates),
    }),
    [syncPositionState, stopLoadWatch]
  )

  const guardedEvents = useCallback(
    (kind: EngineKind): EngineEvents => {
      const active = () => engineKindRef.current === kind
      const base = engineEventBodies
      return {
        onPlay: () => active() && base.onPlay(),
        onPause: () => active() && base.onPause(),
        onPlaying: () => active() && base.onPlaying(),
        onWaiting: () => active() && base.onWaiting(),
        onCanPlay: () => active() && base.onCanPlay(),
        onTimeUpdate: (seconds) => active() && base.onTimeUpdate(seconds),
        onDurationChange: (seconds) => {
          if (kind === 'native') setNativeDuration(seconds)
          if (active()) base.onDurationChange(seconds)
        },
        onRateChange: (rate) => active() && base.onRateChange(rate),
        onSeeked: () => active() && base.onSeeked(),
        onVolumeChange: (volume, isMuted) => active() && base.onVolumeChange(volume, isMuted),
        onError: (message) => active() && base.onError(message),
        onEnded: () => active() && base.onEnded(),
        onAvailableRatesChange: (rates) => active() && base.onAvailableRatesChange(rates),
        onTimelineHoldChange: (holding) => kind === 'native' && setTimelineHold(holding),
      }
    },
    [engineEventBodies]
  )

  // The native engine binds to the stable <video> once it mounts. A
  // replacement (StrictMode's dev remount) inherits its predecessor's state,
  // so a restore's deferred seek survives the swap.
  const nativeHandoffRef = useRef<NativeEngineState | null>(null)
  useEffect(() => {
    const el = mediaRef.current
    if (!el) return
    const engine = new NativeEngine(el, guardedEvents('native'), nativeHandoffRef.current)
    nativeEngineRef.current = engine
    return () => {
      nativeHandoffRef.current = engine.destroy()
      nativeEngineRef.current = null
    }
  }, [guardedEvents])

  // The YouTube engine is created lazily on first use (spec #62 §5) and
  // then kept for the session.
  const ensureYouTubeEngine = useCallback((): YouTubeEngine | null => {
    if (youtubeEngineRef.current) return youtubeEngineRef.current
    const container = youtubeContainerRef.current
    if (!container) return null
    youtubeEngineRef.current = new YouTubeEngine(container, guardedEvents('youtube'))
    return youtubeEngineRef.current
  }, [guardedEvents])

  const currentEngine = useCallback(() => {
    return engineKindRef.current === 'youtube' ? youtubeEngineRef.current : nativeEngineRef.current
  }, [])

  // Assign a new native source and (optionally) carry seek/rate across
  // the transition (the engine defers the seek to loadedmetadata).
  const beginSource = useCallback(
    (source: ActiveSource, seekTo: number | null, rate: number | null, holdForTimeline = false) => {
    // Spec #116 — only the audio rendition of a plain file is ever aligned,
    // so only that may load with `crossorigin` (and only for a host already
    // known to allow it).
    const eligible = source.rendition === 'audio' && !/mpegurl/i.test(source.mimeType ?? '')
    const crossOrigin = beginAudioLoad(source.url, eligible)
    // An http enclosure is loaded through our https redirect (it cannot be
    // loaded into this page as it is); the source keeps its own address as
    // its identity.
    const elementUrl = source.rendition === 'audio' ? elementAudioUrl(source.url, trackRef.current) : source.url
    nativeEngineRef.current?.load(
      { url: source.url, mimeType: source.mimeType, crossOrigin, elementUrl },
      { seekTo, rate, holdForTimeline: holdForTimeline && eligible },
    )
    srcRef.current = source.url
    // A wait already under way goes on for the new load, from the start
    // (a held start is reloaded once so that it can be checked).
    if (loadWatch.current !== undefined) armLoadWatch()
    setNativeDuration(null)
    setMediaError(null)
    // The engine drops its map with the old source.
    setTimelineApplied(false)
    setTimelineMap(null)
    },
    [setTimelineMap, armLoadWatch],
  )

  // Spec #116 — a start or seek to a transcript time waits, silent, for the
  // map when one is expected for this episode's audio and not yet in force.
  const shouldHoldFor = useCallback((episodeId: string | undefined, rendition: RenditionKind) => {
    return rendition === 'audio' && isTimelineExpected(episodeId) && !nativeEngineRef.current?.hasTimeline()
  }, [])

  const releaseTimelineHold = useCallback((episodeId: string, audioUrl: string) => {
    if (trackRef.current?.episodeId !== episodeId) return
    nativeEngineRef.current?.releaseTimelineHold(audioUrl)
  }, [])

  const reloadHeldForCheck = useCallback(
    (episodeId: string, audioUrl: string) => {
      const native = nativeEngineRef.current
      const current = trackRef.current
      if (!native || !current || current.episodeId !== episodeId || srcRef.current !== audioUrl) return false
      if (engineKindRef.current !== 'native' || renditionRef.current !== 'audio') return false
      const asked = native.heldTranscriptTime()
      if (asked === null) return false
      const source = selectSource(current, 'audio')
      if (source.url !== audioUrl) return false
      // A held seek made while paused stays paused: the reload must not
      // turn it into a start.
      const wantsPlay = native.isPlaying()
      beginSource(source, asked, native.getRate(), true)
      if (wantsPlay) native.play().catch(() => setIsPlaying(false))
      return true
    },
    [beginSource],
  )

  const applyTimeline = useCallback((episodeId: string, audioUrl: string, timeline: AppliedTimeline | null) => {
    const native = nativeEngineRef.current
    if (!native) return false
    if (trackRef.current?.episodeId !== episodeId || srcRef.current !== audioUrl) return false
    // Lifting must always reach the source the map was set on, even while
    // it is parked: after a switch to the YouTube player the native engine
    // keeps its source, and a switch back reuses it. A map found invalid in
    // the meantime must not come back with it.
    if (timeline !== null && (engineKindRef.current !== 'native' || renditionRef.current !== 'audio')) return false
    if (!native.setTimeline(audioUrl, timeline)) return false
    setTimelineApplied(timeline !== null)
    const map = timeline?.map ?? null
    setTimelineMap(map)
    // The map may arrive in the middle of an ad; do not wait for a tick.
    if (map !== null) setListenerAd(map.listenerAdAt(native.getElementTime()))
    return true
  }, [setTimelineMap])

  // Spec #90 — the persistence layer reads and drives the player through
  // this port. Built over refs and stable callbacks, so it is created once.
  const port = useMemo<PlayerPort>(
    () => ({
      getTrack: () => trackRef.current,
      // Spec #116 — the saved position is where the AUDIO is, also while a
      // timeline map is in force (getElementTime): a resume seeks before
      // any map exists, so only a position in the audio itself comes back
      // to the same place when the copy is unchanged.
      getLogicalPosition: () => {
        if (engineKindRef.current === 'youtube') return Math.max(0, youtubeEngineRef.current?.getCurrentTime() ?? 0)
        return Math.max(0, (nativeEngineRef.current?.getElementTime() ?? 0) - activeOffsetRef.current)
      },
      getLogicalDuration: () => {
        const engineDuration =
          (engineKindRef.current === 'youtube'
            ? youtubeEngineRef.current?.getDuration()
            : nativeEngineRef.current?.getElementDuration()) ?? 0
        if (!Number.isFinite(engineDuration) || engineDuration <= 0) return null
        const logical = engineKindRef.current === 'youtube' ? engineDuration : engineDuration - activeOffsetRef.current
        return logical > 0 ? logical : null
      },
      isPlaying: () => currentEngine()?.isPlaying() ?? false,
      isEnded: () => engineKindRef.current === 'native' && Boolean(mediaRef.current?.ended),
      getRendition: () => renditionRef.current,
      // Restored sessions always start on the native engine; the YouTube
      // rendition needs a visible surface and is opt-in per episode.
      restore: (next, { logicalPosition, rendition, autoplay }) => {
        const native = nativeEngineRef.current
        if (!native || trackRef.current) return
        const desired = selectSource(next, rendition ?? (trackMediaKind(next) === 'video' ? 'video' : 'audio'))
        const seekTo = logicalPosition > 0 ? logicalPosition + desired.offset : null
        setRendition(desired.rendition)
        activeOffsetRef.current = desired.offset
        trackRef.current = next
        setTrack(next)
        setCurrentTime(seekTo ?? 0)
        setDuration(next.durationHint ?? 0)
        beginSource(desired, seekTo, preferredRateRef.current)
        // Best-effort: a refused autoplay (NotAllowedError) leaves the bar
        // paused at the position — the guaranteed outcome, not an error.
        if (autoplay) native.play().catch(() => setIsPlaying(false))
      },
      applyLateSeek: (episodeId, logicalPosition) => {
        if (trackRef.current?.episodeId !== episodeId || engineKindRef.current !== 'native') return false
        return nativeEngineRef.current?.setPendingSeek(logicalPosition + activeOffsetRef.current) ?? false
      },
      rewindToStart: () => {
        const engine = currentEngine()
        if (!engine) return
        engine.pause()
        const top = engineKindRef.current === 'youtube' ? 0 : activeOffsetRef.current
        engine.seekTo(top)
        setCurrentTime(top)
      },
    }),
    [beginSource, currentEngine, preferredRateRef, setRendition]
  )
  // Declared after the native-engine effect: its restore effect needs the
  // engine to exist.
  const persistence = usePlaybackPersistence(port)
  useLayoutEffect(() => {
    persistenceRef.current = persistence
  }, [persistence])

  const play = useCallback((next: PlayerTrack, options?: PlayOptions) => {
    const native = nativeEngineRef.current
    if (!native) return
    const current = trackRef.current
    if (current && current.episodeId === next.episodeId) {
      // Same episode — adopt the (possibly richer) track object so a
      // manifest learned later (e.g. reader page after a search-result
      // play) upgrades mediaKind without restarting playback. When the
      // prior track had no manifest, its 'audio' rendition was a fallback
      // rather than a user choice — adopt the manifest's kind; otherwise
      // respect the rendition the user is on.
      const hadManifest = Boolean(current.playback)
      persistence.noteUserAction()
      trackRef.current = next
      setTrack(next)
      if (engineKindRef.current === 'youtube') {
        // Resume on the user's chosen YouTube rendition (spec #62). An
        // explicit startAt is applied best-effort — the YouTube timeline
        // has no offset mapping (§8).
        const yt = youtubeEngineRef.current
        if (options?.startAt !== undefined && Number.isFinite(options.startAt)) {
          yt?.seekTo(Math.max(0, options.startAt))
        } else if (options?.restart) {
          yt?.seekTo(0)
        }
        void yt?.play()
        return
      }
      const preferred: RenditionKind =
        !hadManifest && trackMediaKind(next) === 'video' ? 'video' : renditionRef.current
      const desired = selectSource(next, preferred)
      if (desired.rendition !== renditionRef.current) setRendition(desired.rendition)
      if (srcRef.current !== desired.url && srcRef.current !== null) {
        // Spec #61 §5 — the historical branch resumed without ever
        // comparing the source URL; a rendition switch would silently
        // keep playing the old source. Controlled transition instead,
        // preserving logical position and rate.
        const resumeAt =
          options?.startAt !== undefined && Number.isFinite(options.startAt)
            ? Math.max(0, options.startAt)
            : options?.restart
              ? 0
              : Math.max(0, native.getCurrentTime() - activeOffsetRef.current + desired.offset)
        const rate = native.getRate()
        activeOffsetRef.current = desired.offset
        beginSource(desired, resumeAt, rate)
        awaitAudio(native.play())
        return
      }
      if (options?.startAt !== undefined && Number.isFinite(options.startAt)) {
        native.seekTo(Math.max(0, options.startAt), {
          holdForTimeline: shouldHoldFor(next.episodeId, desired.rendition),
        })
      } else if (options?.restart) {
        native.seekTo(0)
      }
      // Still inside user-gesture stack.
      awaitAudio(native.play())
      return
    }
    // New episode — always starts on the native engine (the YouTube
    // rendition is opt-in per episode, never a sticky default). Assign
    // source and call play() synchronously so the initial play request
    // stays inside the click handler, otherwise Safari/iOS reject it as
    // autoplay (NotAllowedError).
    const kind = trackMediaKind(next)
    const desired = selectSource(next, kind === 'video' ? 'video' : 'audio')
    // Spec #90 — flushes the outgoing track while the engines still report
    // it, then resolves where this one starts: an explicit startAt, else the
    // stored position (logical → this asset's timeline), else the top.
    const seekTo = persistence.startTrack(next, options, desired.offset)
    // Spec #116 — an explicit startAt is a transcript time the listener
    // picked. When a map is expected, hold the start for it.
    const asked = options?.startAt !== undefined && Number.isFinite(options.startAt) ? Math.max(0, options.startAt) : null
    // The engine may still hold the previous episode's map at this point;
    // it says nothing about this one, whose source is about to replace it.
    const holdStart = asked !== null && desired.rendition === 'audio' && isTimelineExpected(next.episodeId)
    if (engineKindRef.current === 'youtube') {
      youtubeEngineRef.current?.pause()
      setEngineKind('native')
      setAvailableRates(null)
    }
    setRendition(desired.rendition)
    activeOffsetRef.current = desired.offset
    trackRef.current = next
    setTrack(next)
    setCurrentTime(seekTo ?? 0)
    setDuration(next.durationHint ?? 0)
    // Spec #72 §5 — a fresh source starts at the persisted preference.
    beginSource(desired, holdStart ? (seekTo ?? asked) : seekTo, preferredRateRef.current, holdStart)
    awaitAudio(native.play())
  }, [beginSource, persistence, preferredRateRef, setEngineKind, setRendition, shouldHoldFor, awaitAudio])

  // A pause the listener asked for ends the wait for audio too: the button
  // must go back to Play, not stay on its spinner.
  const stopWaiting = useCallback(() => {
    setIsLoading(false)
    stopLoadWatch()
  }, [stopLoadWatch])

  const pause = useCallback(() => {
    stopWaiting()
    currentEngine()?.pause()
  }, [currentEngine, stopWaiting])

  const resume = useCallback(() => {
    awaitAudio(currentEngine()?.play())
  }, [currentEngine, awaitAudio])

  const toggle = useCallback(() => {
    const engine = currentEngine()
    if (!engine) return
    // Also while a start is still on its way and the engine does not count
    // as playing yet (an hls.js attach, the YouTube player buffering): a
    // press on the spinning button withdraws it.
    if (engine.isPlaying() || (isLoading && isPlaying)) {
      stopWaiting()
      engine.pause()
    } else {
      awaitAudio(engine.play())
    }
  }, [currentEngine, awaitAudio, stopWaiting, isLoading, isPlaying])

  const seek = useCallback(
    (seconds: number, options?: { transcript?: boolean }) => {
      if (
        options?.transcript &&
        engineKindRef.current === 'native' &&
        shouldHoldFor(trackRef.current?.episodeId, renditionRef.current)
      ) {
        nativeEngineRef.current?.seekTo(seconds, { holdForTimeline: true })
        return
      }
      currentEngine()?.seekTo(seconds)
    },
    [currentEngine, shouldHoldFor]
  )

  const skip = useCallback(
    (deltaSeconds: number) => {
      const engine = currentEngine()
      if (!engine) return
      const duration = engine.getDuration() || Infinity
      engine.seekTo(Math.min(duration, Math.max(0, engine.getCurrentTime() + deltaSeconds)))
    },
    [currentEngine]
  )

  const setRate = useCallback(
    (rate: number) => {
      // Explicit choice → the preference. Engine-side snapping (YouTube
      // clamping to its list) reports back through onRateChange only.
      persistRate(rate)
      currentEngine()?.setRate(rate)
    },
    [currentEngine, persistRate]
  )

  const stop = useCallback(() => {
    // Spec #90 — record the Stop before teardown: it needs the track and
    // position, and hides the episode from restore on every device.
    persistence.onStop()
    // Full session teardown: the iframe player is destroyed (spec #62 §5 —
    // this is the one path that does), the native engine just detaches its
    // source; the stable nodes themselves stay mounted.
    setEngineKind('native')
    youtubeEngineRef.current?.destroy()
    youtubeEngineRef.current = null
    nativeEngineRef.current?.unload()
    if (typeof document !== 'undefined' && 'exitPictureInPicture' in document && document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {})
    }
    trackRef.current = null
    srcRef.current = null
    activeOffsetRef.current = 0
    setRendition('audio')
    setTrack(null)
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(0)
    setNativeDuration(null)
    setMediaError(null)
    setIsLoading(false)
    stopLoadWatch()
    setTimelineApplied(false)
    setTimelineMap(null)
    setTimelineHold(false)
    setAvailableRates(null)
  }, [persistence, setEngineKind, setRendition, setTimelineMap, stopLoadWatch])

  const isCurrent = useCallback(
    (episodeId: string) => track?.episodeId === episodeId,
    [track]
  )

  // Stable across re-renders so the rAF loop's effect doesn't tear down
  // and respawn whenever PlayerContextValue changes identity. Reads the
  // refs each call rather than capturing an engine. On the YouTube engine
  // this is the ~250 ms polled + interpolated clock (spec #62 §8) — same
  // synchronous contract, degraded honesty.
  const getCurrentTime = useCallback(() => {
    return currentEngine()?.getCurrentTime() ?? 0
  }, [currentEngine])

  const getAssetTime = useCallback(() => {
    return engineKindRef.current === 'native' ? (nativeEngineRef.current?.getElementTime() ?? 0) : 0
  }, [])

  const getAssetDuration = useCallback(() => {
    const seconds = engineKindRef.current === 'native' ? (nativeEngineRef.current?.getElementDuration() ?? 0) : 0
    return seconds > 0 ? seconds : null
  }, [])

  // Spec #61 §5 — "Use audio rendition": a controlled source transition to
  // the other rendition preserving logical position/rate/play state
  // (offset-adjusted). Distinct from the visual-off videoPreference toggle.
  // Leaving the YouTube engine (spec #62): the iframe clock is read as
  // logical time (best-effort — ad drift accepted, §8), the iframe pauses
  // but survives for an instant switch-back, and the native engine resumes
  // at logical + target-asset offset.
  const switchRendition = useCallback((target: RenditionKind) => {
    const native = nativeEngineRef.current
    const current = trackRef.current
    if (!native || !current) return

    if (engineKindRef.current === 'youtube') {
      // Exiting the iframe must NEVER bail (§7 depends on it): whatever
      // native source selectSource resolves for the request — the audio
      // asset, the native video asset, or the legacy audio_url — is an
      // acceptable landing.
      const desired = selectSource(current, target)
      const yt = youtubeEngineRef.current
      const wasPlaying = yt?.isPlaying() ?? false
      const logical = Math.max(0, yt?.getCurrentTime() ?? 0)
      const rate = yt?.getRate() ?? 1
      yt?.pause()
      setEngineKind('native')
      setAvailableRates(null)
      setRendition(desired.rendition)
      const engineTime = Math.max(0, logical + desired.offset)
      activeOffsetRef.current = desired.offset
      if (desired.url === srcRef.current) {
        native.seekTo(engineTime)
        native.setRate(rate)
      } else {
        beginSource(desired, engineTime, rate)
      }
      if (wasPlaying) awaitAudio(native.play())
      else setIsPlaying(false)
      return
    }

    const desired = selectSource(current, target)
    if (desired.rendition !== target) return // requested rendition unavailable
    setRendition(target)
    if (desired.url === srcRef.current) {
      activeOffsetRef.current = desired.offset
      return
    }
    const wasPlaying = native.isPlaying()
    const resumeAt = Math.max(0, native.getCurrentTime() - activeOffsetRef.current + desired.offset)
    const rate = native.getRate()
    activeOffsetRef.current = desired.offset
    beginSource(desired, resumeAt, rate)
    if (wasPlaying) awaitAudio(native.play())
  }, [beginSource, setEngineKind, setRendition, awaitAudio])

  // Spec #62 §6 — user-gesture entry into the YouTube rendition. Carries
  // the current logical position best-effort onto the YouTube timeline
  // (no offset mapping exists there — §8); the native engine pauses with
  // its source retained for an instant switch-back.
  const playYouTube = useCallback(
    (next: PlayerTrack) => {
      const videoId = next.playback?.youtube?.video_id
      if (!videoId) return
      const engine = ensureYouTubeEngine()
      if (!engine) return
      const native = nativeEngineRef.current
      const current = trackRef.current

      // Opting into the YouTube rendition is an explicit "show me video":
      // clear any earlier "Hide video" state, or the §7 compliance effect
      // would see an unpresented iframe and bounce straight back to audio
      // (the click would be a visible no-op).
      setVideoPreference('shown')
      // A native PiP window survives CSS hiding — close it, or a stale
      // second video surface stays open next to the iframe.
      if (
        typeof document !== 'undefined' &&
        'exitPictureInPicture' in document &&
        document.pictureInPictureElement === mediaRef.current
      ) {
        document.exitPictureInPicture().catch(() => {})
      }

      let startAt = 0
      // Spec #72 §5 — a fresh YouTube session starts at the preference;
      // same-episode entry carries the native engine's live rate instead.
      let rate: number | null = preferredRateRef.current
      if (current && current.episodeId === next.episodeId) {
        persistence.noteUserAction()
        if (engineKindRef.current === 'youtube') {
          // Already on the YouTube rendition — just make sure it plays.
          trackRef.current = next
          setTrack(next)
          void engine.play()
          return
        }
        startAt = Math.max(0, (native?.getCurrentTime() ?? 0) - activeOffsetRef.current)
        rate = native?.getRate() ?? null
      } else {
        // A new episode enters YouTube at the top (no offset mapping to
        // resume onto, spec #62 §8); the outgoing track is flushed first.
        persistence.startTrack(next, { restart: true }, 0)
        setCurrentTime(0)
        setDuration(next.durationHint ?? 0)
      }
      native?.pause()
      trackRef.current = next
      setTrack(next)
      setEngineKind('youtube')
      // The YouTube asset is a video rendition of the session; the native
      // audio asset remains the switch-back target (§7 policy relies on
      // switchRendition('audio') always being available).
      setRendition('video')
      setMediaError(null)
      engine.load({ videoId }, { seekTo: startAt, rate, autoplay: true })
    },
    [ensureYouTubeEngine, persistence, preferredRateRef, setEngineKind, setRendition]
  )

  const registerTheaterSlot = useCallback((episodeId: string, el: HTMLElement) => {
    theaterSlotRef.current = { episodeId, el }
    setTheaterSlot({ episodeId, el })
    return () => {
      if (theaterSlotRef.current?.el === el) theaterSlotRef.current = null
      setTheaterSlot((cur) => (cur && cur.el === el ? null : cur))
    }
  }, [])

  const hasTheaterSlot = useCallback(() => theaterSlotRef.current !== null, [])

  const registerFloatingSlot = useCallback((el: HTMLElement) => {
    // The ref is written synchronously so the §7 compliance effect (which
    // runs after child effects in the same commit) can distinguish "tile
    // mounted and registered this commit" from "no tile will present".
    floatingSlotRef.current = el
    setFloatingSlot(el)
    return () => {
      if (floatingSlotRef.current === el) floatingSlotRef.current = null
      setFloatingSlot((cur) => (cur === el ? null : cur))
    }
  }, [])

  const pipSupported =
    typeof document !== 'undefined' &&
    'pictureInPictureEnabled' in document &&
    document.pictureInPictureEnabled

  const requestPip = useCallback(() => {
    const el = mediaRef.current
    if (!el || typeof document === 'undefined') return
    // Native-engine feature only — the iframe owns its own video (spec #62
    // §7 lists no-native-PiP as an accepted YouTube-rendition gap).
    if (engineKindRef.current !== 'native') return
    if (!('pictureInPictureEnabled' in document) || !document.pictureInPictureEnabled) return
    if (document.pictureInPictureElement === el) {
      document.exitPictureInPicture().catch(() => {})
      return
    }
    // Needs transient user activation and can reject — outcomes are read
    // from the enter/leave events, never assumed (spec #61 invariant 5).
    el.requestPictureInPicture?.().catch(() => {})
  }, [])

  const setVolume = useCallback(
    (next: number) => {
      currentEngine()?.setVolume(next)
    },
    [currentEngine]
  )

  const toggleMute = useCallback(() => {
    currentEngine()?.setMuted(!muted)
  }, [currentEngine, muted])

  // PiP state follows browser events — never assumed (invariant 5).
  useEffect(() => {
    const el = mediaRef.current
    if (!el) return
    const onEnter = () => setPipActive(true)
    const onLeave = () => setPipActive(false)
    el.addEventListener('enterpictureinpicture', onEnter)
    el.addEventListener('leavepictureinpicture', onLeave)
    return () => {
      el.removeEventListener('enterpictureinpicture', onEnter)
      el.removeEventListener('leavepictureinpicture', onLeave)
    }
  }, [])

  // --- Media Session (spec #61 §2, Increment 1) --------------------------
  useEffect(() => {
    setMediaSessionMetadata(
      track
        ? {
            title: track.title,
            podcastTitle: track.podcastTitle,
            artworkUrl: track.artworkUrl,
          }
        : null
    )
  }, [track])

  useEffect(() => {
    setMediaSessionPlaybackState(track ? (isPlaying ? 'playing' : 'paused') : 'none')
  }, [track, isPlaying])

  useEffect(() => {
    setMediaSessionActionHandlers({
      play: () => resume(),
      pause: () => pause(),
      stop: () => stop(),
      seekbackward: (details) => skip(-(details?.seekOffset ?? 15)),
      seekforward: (details) => skip(details?.seekOffset ?? 15),
      seekto: (details) => {
        if (details?.seekTime != null) seek(details.seekTime)
      },
    })
    return () => {
      setMediaSessionActionHandlers({
        play: null,
        pause: null,
        stop: null,
        seekbackward: null,
        seekforward: null,
        seekto: null,
      })
    }
  }, [resume, pause, stop, skip, seek])

  // --- Presentation state machine (spec #61 §3) --------------------------
  const mediaKind = trackMediaKind(track)
  // The YouTube engine is always a visual rendition regardless of the
  // manifest's kind (the current corpus is audio-kind episodes carrying a
  // youtube asset — spec #62 §6).
  const videoPresentable =
    track !== null && ((mediaKind === 'video' && activeRendition === 'video') || activeEngine === 'youtube')
  const theaterSlotActive =
    videoPresentable && theaterSlot !== null && track !== null && theaterSlot.episodeId === track.episodeId

  let presentation: Presentation = 'hidden'
  if (videoPresentable) {
    if (pipActive) presentation = 'native-pip'
    else if (theaterSlotActive) presentation = 'theater'
    else if (videoPreference === 'shown') presentation = 'floating'
  }

  // Spec #62 §7 — the hard compliance rule: the YouTube iframe may only
  // play while visibly presented. Any transition that leaves it
  // unpresented (tile closed, "Hide video", mobile navigation away, the
  // reader overlay unmounting the tile) switches the session to the
  // native audio rendition instead of hiding a playing iframe
  // (hidden-but-audible = background playback of separated audio, both
  // prohibited). Presence is judged from the synchronous slot mirrors —
  // a surface registered in this same commit counts as presented even
  // though the slot state (and thus `presentation`) lags one commit.
  useEffect(() => {
    if (activeEngine !== 'youtube') return
    const current = trackRef.current
    if (!current) return
    const theaterPresented =
      videoPreference === 'shown' &&
      theaterSlotRef.current !== null &&
      theaterSlotRef.current.episodeId === current.episodeId
    const floatingPresented = videoPreference === 'shown' && floatingSlotRef.current !== null
    if (!theaterPresented && !floatingPresented) switchRendition('audio')
  }, [activeEngine, presentation, theaterSlot, floatingSlot, videoPreference, switchRendition])

  // Where the media layer should place the stable video node. During native
  // PiP the browser shows its own window; the in-page element stays parked
  // over the theater slot (browser renders its own placeholder there) or is
  // hidden when no slot exists.
  const positionTarget: HTMLElement | null =
    presentation === 'theater'
      ? theaterSlot?.el ?? null
      : presentation === 'floating'
        ? floatingSlot
        : presentation === 'native-pip' && theaterSlotActive
          ? theaterSlot?.el ?? null
          : null

  // Position the stable node over the active slot. A rAF loop (only while a
  // visual surface is active) follows the slot through scrolling, sticky
  // positioning, overlay panels and the draggable tile without ever
  // reparenting the video (invariant 2).
  useEffect(() => {
    const layer = layerRef.current
    if (!layer) return
    if (!positionTarget) {
      layer.style.visibility = 'hidden'
      layer.style.pointerEvents = 'none'
      layer.style.width = '0px'
      layer.style.height = '0px'
      return
    }
    layer.style.visibility = 'visible'
    layer.style.pointerEvents = 'auto'
    // One rung above the fixed surface hosting the slot (spec #71 ladder,
    // resolved in constants/layers.ts): 60 inside the z-[45] reader
    // overlay, 71 inside the z-[70] Now Playing sheet (spec #72 2c), the
    // shell rung 40 in page content beside the floating tile.
    layer.style.zIndex = String(mediaLayerZIndex(positionTarget))
    let handle = 0
    const tick = () => {
      const rect = positionTarget.getBoundingClientRect()
      layer.style.transform = `translate(${rect.left}px, ${rect.top}px)`
      layer.style.width = `${rect.width}px`
      layer.style.height = `${rect.height}px`
      handle = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(handle)
  }, [positionTarget])

  const markListened = persistence.markListened

  const showNativeControls = presentation === 'theater' || presentation === 'floating'
  const captionsUrl = track?.playback?.captions_url ?? null
  const posterUrl = track?.playback?.poster_url ?? track?.artworkUrl ?? undefined

  // Memoize so the state-context value identity only changes when
  // low-frequency state changes. currentTime is delivered via a separate
  // context so high-frequency ticks don't re-render PlayerContext consumers.
  const value = useMemo<PlayerContextValue>(
    () => ({
      track,
      isPlaying,
      // A held start (spec #116) is a wait like buffering: same spinner.
      // Waiting only counts while playback is wanted: a start that was
      // refused or paused along the way (the engine reports it paused) must
      // not leave the buttons spinning.
      isLoading: (isLoading && isPlaying) || timelineHold,
      duration,
      nativeDuration,
      playbackRate,
      availableRates,
      play,
      pause,
      resume,
      toggle,
      seek,
      skip,
      setRate,
      stop,
      markListened,
      isCurrent,
      getCurrentTime,
      mediaKind,
      presentation,
      videoPreference,
      setVideoPreference,
      activeRendition,
      canSwitchRendition: Boolean(track?.playback?.audio && track?.playback?.video),
      switchRendition,
      activeEngine,
      youtubeAvailable: Boolean(track?.playback?.youtube),
      playYouTube,
      registerTheaterSlot,
      registerFloatingSlot,
      hasTheaterSlot,
      // PiP is a native-engine feature; while the iframe renders, the
      // affordance disappears rather than silently failing (spec #62 §7).
      pipSupported: pipSupported && activeEngine !== 'youtube',
      pipActive,
      requestPip,
      applyTimeline,
      timelineApplied,
      timelineMap: activeEngine === 'native' ? timelineMap : null,
      listenerAd: activeEngine === 'native' ? listenerAd : null,
      getAssetTime,
      getAssetDuration,
      timelineHold,
      releaseTimelineHold,
      reloadHeldForCheck,
      mediaError,
      volume,
      muted,
      setVolume,
      toggleMute,
    }),
    [
      track,
      isPlaying,
      isLoading,
      duration,
      nativeDuration,
      playbackRate,
      availableRates,
      play,
      pause,
      resume,
      toggle,
      seek,
      skip,
      setRate,
      stop,
      markListened,
      isCurrent,
      getCurrentTime,
      mediaKind,
      presentation,
      videoPreference,
      setVideoPreference,
      activeRendition,
      switchRendition,
      activeEngine,
      playYouTube,
      registerTheaterSlot,
      registerFloatingSlot,
      hasTheaterSlot,
      pipSupported,
      pipActive,
      requestPip,
      applyTimeline,
      timelineApplied,
      timelineMap,
      listenerAd,
      getAssetTime,
      getAssetDuration,
      timelineHold,
      releaseTimelineHold,
      reloadHeldForCheck,
      mediaError,
      volume,
      muted,
      setVolume,
      toggleMute,
    ]
  )

  return (
    <PlayerContext.Provider value={value}>
      <PlayerTimeContext.Provider value={currentTime}>
        {children}
      </PlayerTimeContext.Provider>
      {/* Global media layer — the one stable media node (invariant 2).
          Hidden (but never unmounted) while presentation is audio-first. */}
      <div
        ref={layerRef}
        data-testid="player-media-layer"
        aria-hidden={showNativeControls ? undefined : true}
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 0,
          height: 0,
          visibility: 'hidden',
          pointerEvents: 'none',
          overflow: 'hidden',
          willChange: 'transform',
        }}
      >
        {/* Engine events are bound by NativeEngine (addEventListener), not
            JSX props — both engines feed the same guarded EngineEvents. */}
        <video
          ref={mediaRef}
          preload="metadata"
          playsInline
          controls={showNativeControls && activeEngine === 'native'}
          poster={posterUrl}
          tabIndex={showNativeControls && activeEngine === 'native' ? 0 : -1}
          crossOrigin={captionsUrl ? 'anonymous' : undefined}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            backgroundColor: '#000',
            display: activeEngine === 'native' ? undefined : 'none',
          }}
        >
          {captionsUrl ? (
            <track kind="captions" src={captionsUrl} default />
          ) : null}
        </video>
        {/* Spec #62 §5 — second stable node: the YouTube iframe container.
            Always mounted (an empty div is free); the IFrame API replaces
            it with the player on first YouTube playback and it is never
            reparented afterwards. At most one node is visible. */}
        <div
          data-testid="player-youtube-layer"
          style={{
            width: '100%',
            height: '100%',
            backgroundColor: '#000',
            display: activeEngine === 'youtube' ? undefined : 'none',
          }}
        >
          <div ref={youtubeContainerRef} style={{ width: '100%', height: '100%' }} />
        </div>
      </div>
    </PlayerContext.Provider>
  )
}

export function usePlayer(): PlayerContextValue {
  const ctx = useContext(PlayerContext)
  if (!ctx) {
    throw new Error('usePlayer must be used within a PlayerProvider')
  }
  return ctx
}

export function usePlayerTime(): number {
  return useContext(PlayerTimeContext)
}
