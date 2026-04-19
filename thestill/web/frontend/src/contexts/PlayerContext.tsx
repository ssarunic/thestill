import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
  currentTime: number
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

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [track, setTrack] = useState<PlayerTrack | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    if (!track) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
      return
    }
    const absolute = new URL(track.audioUrl, window.location.href).href
    if (audio.src !== absolute) {
      audio.src = track.audioUrl
      setCurrentTime(0)
      setDuration(track.durationHint ?? 0)
      audio.play().catch(() => {
        setIsPlaying(false)
      })
    }
  }, [track])

  const play = useCallback((next: PlayerTrack) => {
    setTrack((current) => {
      if (current && current.episodeId === next.episodeId) {
        audioRef.current?.play().catch(() => setIsPlaying(false))
        return current
      }
      return next
    })
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
    setTrack(null)
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [])

  const isCurrent = useCallback(
    (episodeId: string) => track?.episodeId === episodeId,
    [track]
  )

  return (
    <PlayerContext.Provider
      value={{
        track,
        isPlaying,
        isLoading,
        currentTime,
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
      }}
    >
      {children}
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
