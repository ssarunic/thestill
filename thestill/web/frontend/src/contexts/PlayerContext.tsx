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

// ---------------------------------------------------------------------------
// Spec #61 — unified audio/video playback session.
//
// One logical playback session, exactly one active playback engine: a single
// stable <video> element (HTMLMediaElement plays audio resources fine) that
// is created once inside a global media layer and NEVER reparented — React
// portals with changing targets remount their children, and moving a <video>
// in the DOM pauses playback per the HTML spec. Presentation surfaces
// (theater slot in the episode reader, the floating tile) register a
// rectangle; the media layer positions the stable node over the active slot.
// Presentation is modeled separately from playback: which surface shows the
// video is a UI state machine; play/pause/position/rate belong to the
// session and are never affected by surface changes.
// ---------------------------------------------------------------------------

export type MediaKind = 'audio' | 'video'
export type Presentation = 'hidden' | 'theater' | 'floating' | 'native-pip'
export type VideoPreference = 'shown' | 'audio-only'
export type RenditionKind = 'audio' | 'video'

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

export function PlayerProvider({ children }: { children: ReactNode }) {
  const mediaRef = useRef<HTMLVideoElement | null>(null)
  const layerRef = useRef<HTMLDivElement | null>(null)
  const trackRef = useRef<PlayerTrack | null>(null)
  const pendingSeekRef = useRef<number | null>(null)
  // Last URL we assigned to the element. Compared on same-episode play()
  // calls so a rendition/manifest change becomes a controlled source
  // transition instead of silently resuming the old source (spec #61 §5).
  const srcRef = useRef<string | null>(null)
  const renditionRef = useRef<RenditionKind>('audio')
  // timeline_offset of the asset currently in the engine. Logical time =
  // engineTime - activeOffset; transitions re-seek so it is preserved.
  const activeOffsetRef = useRef(0)

  const [track, setTrack] = useState<PlayerTrack | null>(null)
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

  const setRendition = useCallback((rendition: RenditionKind) => {
    renditionRef.current = rendition
    setActiveRendition(rendition)
  }, [])

  // Assign a new source and (optionally) carry rate/play state across the
  // transition. The seek is deferred to loadedmetadata — browsers silently
  // clamp seeks on unloaded media.
  const beginSource = useCallback(
    (el: HTMLVideoElement, url: string, seekTo: number | null, rate: number | null) => {
      el.src = url
      srcRef.current = url
      pendingSeekRef.current = seekTo
      if (rate != null && Number.isFinite(rate) && rate > 0) {
        // Loading resets playbackRate to defaultPlaybackRate; set both so
        // the carried rate survives the source transition.
        el.defaultPlaybackRate = rate
        el.playbackRate = rate
      }
      setMediaError(null)
    },
    []
  )

  const play = useCallback((next: PlayerTrack, options?: PlayOptions) => {
    const el = mediaRef.current
    if (!el) return
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
            : Math.max(0, el.currentTime - activeOffsetRef.current + desired.offset)
        const rate = el.playbackRate
        activeOffsetRef.current = desired.offset
        beginSource(el, desired.url, resumeAt, rate)
        el.play().catch(() => setIsPlaying(false))
        return
      }
      if (options?.startAt !== undefined && Number.isFinite(options.startAt)) {
        el.currentTime = Math.max(0, options.startAt)
      }
      // Still inside user-gesture stack.
      el.play().catch(() => setIsPlaying(false))
      return
    }
    // New episode — assign source and call play() synchronously so the
    // initial play request stays inside the click handler, otherwise
    // Safari/iOS reject it as autoplay (NotAllowedError).
    const kind = trackMediaKind(next)
    const desired = selectSource(next, kind === 'video' ? 'video' : 'audio')
    setRendition(desired.rendition)
    activeOffsetRef.current = desired.offset
    trackRef.current = next
    setTrack(next)
    setCurrentTime(0)
    setDuration(next.durationHint ?? 0)
    beginSource(
      el,
      desired.url,
      options?.startAt !== undefined && Number.isFinite(options.startAt)
        ? Math.max(0, options.startAt)
        : null,
      null
    )
    el.play().catch(() => setIsPlaying(false))
  }, [beginSource, setRendition])

  const pause = useCallback(() => {
    mediaRef.current?.pause()
  }, [])

  const resume = useCallback(() => {
    mediaRef.current?.play().catch(() => setIsPlaying(false))
  }, [])

  const toggle = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    if (el.paused) {
      el.play().catch(() => setIsPlaying(false))
    } else {
      el.pause()
    }
  }, [])

  const seek = useCallback((seconds: number) => {
    const el = mediaRef.current
    if (!el) return
    if (Number.isFinite(seconds)) {
      el.currentTime = Math.max(0, seconds)
    }
  }, [])

  const skip = useCallback((deltaSeconds: number) => {
    const el = mediaRef.current
    if (!el) return
    const duration = Number.isFinite(el.duration) ? el.duration : Infinity
    el.currentTime = Math.min(duration, Math.max(0, el.currentTime + deltaSeconds))
  }, [])

  const setRate = useCallback((rate: number) => {
    const el = mediaRef.current
    if (!el) return
    el.defaultPlaybackRate = rate
    el.playbackRate = rate
  }, [])

  const stop = useCallback(() => {
    const el = mediaRef.current
    if (el) {
      el.pause()
      el.removeAttribute('src')
      el.load()
    }
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
  }, [setRendition])

  const isCurrent = useCallback(
    (episodeId: string) => track?.episodeId === episodeId,
    [track]
  )

  // Stable across re-renders so the rAF loop's effect doesn't tear down
  // and respawn whenever PlayerContextValue changes identity. Reads the
  // ref each call rather than capturing the media element.
  const getCurrentTime = useCallback(() => {
    return mediaRef.current?.currentTime ?? 0
  }, [])

  // Spec #61 §5 — "Use audio rendition": a controlled source transition to
  // the other rendition preserving logical position/rate/play state
  // (offset-adjusted). Distinct from the visual-off videoPreference toggle.
  const switchRendition = useCallback((target: RenditionKind) => {
    const el = mediaRef.current
    const current = trackRef.current
    if (!el || !current) return
    const desired = selectSource(current, target)
    if (desired.rendition !== target) return // requested rendition unavailable
    setRendition(target)
    if (desired.url === srcRef.current) {
      activeOffsetRef.current = desired.offset
      return
    }
    const wasPlaying = !el.paused && !el.ended
    const resumeAt = Math.max(0, el.currentTime - activeOffsetRef.current + desired.offset)
    const rate = el.playbackRate
    activeOffsetRef.current = desired.offset
    beginSource(el, desired.url, resumeAt, rate)
    if (wasPlaying) el.play().catch(() => setIsPlaying(false))
  }, [beginSource, setRendition])

  const registerTheaterSlot = useCallback((episodeId: string, el: HTMLElement) => {
    setTheaterSlot({ episodeId, el })
    return () => {
      setTheaterSlot((cur) => (cur && cur.el === el ? null : cur))
    }
  }, [])

  const registerFloatingSlot = useCallback((el: HTMLElement) => {
    setFloatingSlot(el)
    return () => {
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
    if (!('pictureInPictureEnabled' in document) || !document.pictureInPictureEnabled) return
    if (document.pictureInPictureElement === el) {
      document.exitPictureInPicture().catch(() => {})
      return
    }
    // Needs transient user activation and can reject — outcomes are read
    // from the enter/leave events, never assumed (spec #61 invariant 5).
    el.requestPictureInPicture?.().catch(() => {})
  }, [])

  const setVolume = useCallback((next: number) => {
    const el = mediaRef.current
    if (!el) return
    el.volume = Math.min(1, Math.max(0, next))
    if (el.volume > 0 && el.muted) el.muted = false
  }, [])

  const toggleMute = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    el.muted = !el.muted
  }, [])

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

  const syncPositionState = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    updateMediaSessionPositionState(el.duration, el.playbackRate, el.currentTime)
  }, [])

  // --- Presentation state machine (spec #61 §3) --------------------------
  const mediaKind = trackMediaKind(track)
  const videoPresentable = track !== null && mediaKind === 'video' && activeRendition === 'video'
  const theaterSlotActive =
    videoPresentable && theaterSlot !== null && track !== null && theaterSlot.episodeId === track.episodeId

  let presentation: Presentation = 'hidden'
  if (videoPresentable) {
    if (pipActive) presentation = 'native-pip'
    else if (theaterSlotActive) presentation = 'theater'
    else if (videoPreference === 'shown') presentation = 'floating'
  }

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
        <video
          ref={mediaRef}
          preload="metadata"
          playsInline
          controls={showNativeControls}
          poster={posterUrl}
          tabIndex={showNativeControls ? 0 : -1}
          crossOrigin={captionsUrl ? 'anonymous' : undefined}
          style={{ width: '100%', height: '100%', objectFit: 'contain', backgroundColor: '#000' }}
          onPlay={() => {
            setIsPlaying(true)
            syncPositionState()
          }}
          onPause={() => setIsPlaying(false)}
          onPlaying={() => {
            setIsPlaying(true)
            setIsLoading(false)
          }}
          onWaiting={() => setIsLoading(true)}
          onCanPlay={() => setIsLoading(false)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => {
            if (pendingSeekRef.current != null) {
              e.currentTarget.currentTime = pendingSeekRef.current
              pendingSeekRef.current = null
            }
          }}
          onDurationChange={(e) => {
            const d = e.currentTarget.duration
            if (Number.isFinite(d)) setDuration(d)
            syncPositionState()
          }}
          onRateChange={(e) => {
            setPlaybackRate(e.currentTarget.playbackRate)
            syncPositionState()
          }}
          onSeeked={syncPositionState}
          onVolumeChange={(e) => {
            setVolumeState(e.currentTarget.volume)
            setMuted(e.currentTarget.muted)
          }}
          onError={(e) => {
            // Only surface errors for a real source — clearing src during
            // stop() fires a spurious error event in some browsers.
            if (srcRef.current !== null) {
              setMediaError(describeMediaError(e.currentTarget.error))
              setIsLoading(false)
            }
          }}
          onEnded={() => {
            setIsPlaying(false)
            setCurrentTime(0)
          }}
        >
          {captionsUrl ? (
            <track kind="captions" src={captionsUrl} default />
          ) : null}
        </video>
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
