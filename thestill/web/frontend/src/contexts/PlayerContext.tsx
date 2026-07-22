import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
import { NativeEngine } from './playback-engine/native-engine'
import type { EngineEvents, EngineKind } from './playback-engine/types'
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
export type Presentation = 'hidden' | 'theater' | 'floating' | 'native-pip'
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
}

export interface PlayerContextValue {
  track: PlayerTrack | null
  isPlaying: boolean
  isLoading: boolean
  duration: number
  playbackRate: number
  play: (track: PlayerTrack, options?: PlayOptions) => void
  pause: () => void
  resume: () => void
  toggle: () => void
  seek: (seconds: number) => void
  skip: (deltaSeconds: number) => void
  setRate: (rate: number) => void
  stop: () => void
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
  // Native PiP — user-initiated, progressive enhancement only (§2).
  // Browser state is authoritative: pipActive follows the
  // enter/leavepictureinpicture events, never assumptions.
  pipSupported: boolean
  pipActive: boolean
  requestPip: () => void
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
      ? { url: asset.url, offset: asset.timeline_offset ?? 0, rendition }
      : null
  }
  return (
    pick(preferred) ??
    pick(preferred === 'video' ? 'audio' : 'video') ?? {
      url: track.audioUrl,
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
  const [playbackRate, setPlaybackRate] = useState(1)
  const [activeRendition, setActiveRendition] = useState<RenditionKind>('audio')
  const [videoPreference, setVideoPreference] = useState<VideoPreference>('shown')
  const [pipActive, setPipActive] = useState(false)
  const [mediaError, setMediaError] = useState<string | null>(null)
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
      },
      onPause: () => setIsPlaying(false),
      onPlaying: () => {
        setIsPlaying(true)
        setIsLoading(false)
      },
      onWaiting: () => setIsLoading(true),
      onCanPlay: () => setIsLoading(false),
      onTimeUpdate: (seconds: number) => setCurrentTime(seconds),
      onDurationChange: (seconds: number) => {
        setDuration(seconds)
        syncPositionState()
      },
      onRateChange: (rate: number) => {
        setPlaybackRate(rate)
        syncPositionState()
      },
      onSeeked: () => syncPositionState(),
      onVolumeChange: (volume: number, muted: boolean) => {
        setVolumeState(volume)
        setMuted(muted)
      },
      onError: (message: string) => {
        setMediaError(message)
        setIsLoading(false)
      },
      onEnded: () => {
        setIsPlaying(false)
        setCurrentTime(0)
      },
    }),
    [syncPositionState]
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
        onDurationChange: (seconds) => active() && base.onDurationChange(seconds),
        onRateChange: (rate) => active() && base.onRateChange(rate),
        onSeeked: () => active() && base.onSeeked(),
        onVolumeChange: (volume, isMuted) => active() && base.onVolumeChange(volume, isMuted),
        onError: (message) => active() && base.onError(message),
        onEnded: () => active() && base.onEnded(),
      }
    },
    [engineEventBodies]
  )

  // The native engine binds to the stable <video> once it mounts.
  useEffect(() => {
    const el = mediaRef.current
    if (!el) return
    const engine = new NativeEngine(el, guardedEvents('native'))
    nativeEngineRef.current = engine
    return () => {
      engine.destroy()
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
  const beginSource = useCallback((url: string, seekTo: number | null, rate: number | null) => {
    nativeEngineRef.current?.load({ url }, { seekTo, rate })
    srcRef.current = url
    setMediaError(null)
  }, [])

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
      trackRef.current = next
      setTrack(next)
      if (engineKindRef.current === 'youtube') {
        // Resume on the user's chosen YouTube rendition (spec #62). An
        // explicit startAt is applied best-effort — the YouTube timeline
        // has no offset mapping (§8).
        const yt = youtubeEngineRef.current
        if (options?.startAt !== undefined && Number.isFinite(options.startAt)) {
          yt?.seekTo(Math.max(0, options.startAt))
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
            : Math.max(0, native.getCurrentTime() - activeOffsetRef.current + desired.offset)
        const rate = native.getRate()
        activeOffsetRef.current = desired.offset
        beginSource(desired.url, resumeAt, rate)
        native.play().catch(() => setIsPlaying(false))
        return
      }
      if (options?.startAt !== undefined && Number.isFinite(options.startAt)) {
        native.seekTo(Math.max(0, options.startAt))
      }
      // Still inside user-gesture stack.
      native.play().catch(() => setIsPlaying(false))
      return
    }
    // New episode — always starts on the native engine (the YouTube
    // rendition is opt-in per episode, never a sticky default). Assign
    // source and call play() synchronously so the initial play request
    // stays inside the click handler, otherwise Safari/iOS reject it as
    // autoplay (NotAllowedError).
    if (engineKindRef.current === 'youtube') {
      youtubeEngineRef.current?.pause()
      setEngineKind('native')
    }
    const kind = trackMediaKind(next)
    const desired = selectSource(next, kind === 'video' ? 'video' : 'audio')
    setRendition(desired.rendition)
    activeOffsetRef.current = desired.offset
    trackRef.current = next
    setTrack(next)
    setCurrentTime(0)
    setDuration(next.durationHint ?? 0)
    beginSource(
      desired.url,
      options?.startAt !== undefined && Number.isFinite(options.startAt)
        ? Math.max(0, options.startAt)
        : null,
      null
    )
    native.play().catch(() => setIsPlaying(false))
  }, [beginSource, setEngineKind, setRendition])

  const pause = useCallback(() => {
    currentEngine()?.pause()
  }, [currentEngine])

  const resume = useCallback(() => {
    currentEngine()
      ?.play()
      .catch(() => setIsPlaying(false))
  }, [currentEngine])

  const toggle = useCallback(() => {
    const engine = currentEngine()
    if (!engine) return
    if (engine.isPlaying()) {
      engine.pause()
    } else {
      engine.play().catch(() => setIsPlaying(false))
    }
  }, [currentEngine])

  const seek = useCallback(
    (seconds: number) => {
      currentEngine()?.seekTo(seconds)
    },
    [currentEngine]
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
      currentEngine()?.setRate(rate)
    },
    [currentEngine]
  )

  const stop = useCallback(() => {
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
    setMediaError(null)
  }, [setEngineKind, setRendition])

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
      setRendition(desired.rendition)
      const engineTime = Math.max(0, logical + desired.offset)
      activeOffsetRef.current = desired.offset
      if (desired.url === srcRef.current) {
        native.seekTo(engineTime)
        native.setRate(rate)
      } else {
        beginSource(desired.url, engineTime, rate)
      }
      if (wasPlaying) native.play().catch(() => setIsPlaying(false))
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
    beginSource(desired.url, resumeAt, rate)
    if (wasPlaying) native.play().catch(() => setIsPlaying(false))
  }, [beginSource, setEngineKind, setRendition])

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
      let rate: number | null = null
      if (current && current.episodeId === next.episodeId) {
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
    [ensureYouTubeEngine, setEngineKind, setRendition]
  )

  const registerTheaterSlot = useCallback((episodeId: string, el: HTMLElement) => {
    theaterSlotRef.current = { episodeId, el }
    setTheaterSlot({ episodeId, el })
    return () => {
      if (theaterSlotRef.current?.el === el) theaterSlotRef.current = null
      setTheaterSlot((cur) => (cur && cur.el === el ? null : cur))
    }
  }, [])

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
    // The reader overlay (spec #52) is a z-50 dialog; a slot registered
    // inside it needs the layer above the scrim. Everywhere else stay
    // below the overlay/command-bar layers (z-50) but above the
    // mini-player bar (z-30).
    layer.style.zIndex = positionTarget.closest('[role="dialog"]') ? '60' : '40'
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
      isLoading,
      duration,
      playbackRate,
      play,
      pause,
      resume,
      toggle,
      seek,
      skip,
      setRate,
      stop,
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
      // PiP is a native-engine feature; while the iframe renders, the
      // affordance disappears rather than silently failing (spec #62 §7).
      pipSupported: pipSupported && activeEngine !== 'youtube',
      pipActive,
      requestPip,
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
      playbackRate,
      play,
      pause,
      resume,
      toggle,
      seek,
      skip,
      setRate,
      stop,
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
      pipSupported,
      pipActive,
      requestPip,
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
