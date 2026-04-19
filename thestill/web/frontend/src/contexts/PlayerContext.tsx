import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export interface PlayerTrack {
  episodeId: string
  podcastSlug: string
  episodeSlug: string
  title: string
  podcastTitle?: string | null
  audioUrl: string
  artworkUrl?: string | null
  durationHint?: number | null
}

interface PlayerContextValue {
  track: PlayerTrack | null
  isPlaying: boolean
  isLoading: boolean
  duration: number
  playbackRate: number
  play: (track: PlayerTrack) => void
  pause: () => void
  resume: () => void
  toggle: () => void
  seek: (seconds: number) => void
  setRate: (rate: number) => void
  stop: () => void
  isCurrent: (episodeId: string) => boolean
}

const PlayerContext = createContext<PlayerContextValue | null>(null)
const PlayerTimeContext = createContext<number>(0)

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const trackRef = useRef<PlayerTrack | null>(null)
  const [track, setTrack] = useState<PlayerTrack | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)

  const play = useCallback((next: PlayerTrack) => {
    const audio = audioRef.current
    if (!audio) return
    const current = trackRef.current
    if (current && current.episodeId === next.episodeId) {
      // Same episode — just resume. Still inside user-gesture stack.
      audio.play().catch(() => setIsPlaying(false))
      return
    }
    // New episode — assign source and call play() synchronously so the
    // initial play request stays inside the click handler, otherwise
    // Safari/iOS reject it as autoplay (NotAllowedError).
    audio.src = next.audioUrl
    trackRef.current = next
    setTrack(next)
    setCurrentTime(0)
    setDuration(next.durationHint ?? 0)
    audio.play().catch(() => setIsPlaying(false))
  }, [])

  const pause = useCallback(() => {
    audioRef.current?.pause()
  }, [])

  const resume = useCallback(() => {
    audioRef.current?.play().catch(() => setIsPlaying(false))
  }, [])

  const toggle = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      audio.play().catch(() => setIsPlaying(false))
    } else {
      audio.pause()
    }
  }, [])

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio) return
    if (Number.isFinite(seconds)) {
      audio.currentTime = Math.max(0, seconds)
    }
  }, [])

  const setRate = useCallback((rate: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.playbackRate = rate
  }, [])

  const stop = useCallback(() => {
    const audio = audioRef.current
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    trackRef.current = null
    setTrack(null)
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [])

  const isCurrent = useCallback(
    (episodeId: string) => track?.episodeId === episodeId,
    [track]
  )

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
      setRate,
      stop,
      isCurrent,
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
      setRate,
      stop,
      isCurrent,
    ]
  )

  return (
    <PlayerContext.Provider value={value}>
      <PlayerTimeContext.Provider value={currentTime}>
        {children}
      </PlayerTimeContext.Provider>
      <audio
        ref={audioRef}
        preload="metadata"
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onPlaying={() => {
          setIsPlaying(true)
          setIsLoading(false)
        }}
        onWaiting={() => setIsLoading(true)}
        onCanPlay={() => setIsLoading(false)}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        onDurationChange={(e) => {
          const d = e.currentTarget.duration
          if (Number.isFinite(d)) setDuration(d)
        }}
        onRateChange={(e) => setPlaybackRate(e.currentTarget.playbackRate)}
        onEnded={() => {
          setIsPlaying(false)
          setCurrentTime(0)
        }}
        style={{ display: 'none' }}
      />
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
