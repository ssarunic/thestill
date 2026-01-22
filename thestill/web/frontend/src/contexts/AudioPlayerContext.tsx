import { createContext, useContext, useRef, useState, useCallback, type ReactNode, type RefObject } from 'react'

interface AudioPlayerContextValue {
  // Ref to the audio element
  audioRef: RefObject<HTMLAudioElement | null>
  // Current playback time in seconds
  currentTime: number
  // Whether audio is currently playing
  isPlaying: boolean
  // Duration of the audio in seconds
  duration: number
  // Seek to a specific time in seconds
  seekTo: (timeInSeconds: number) => void
  // Register the audio element (called by AudioPlayer)
  registerAudio: (element: HTMLAudioElement | null) => void
}

const AudioPlayerContext = createContext<AudioPlayerContextValue | null>(null)

export function AudioPlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [duration, setDuration] = useState(0)

  const registerAudio = useCallback((element: HTMLAudioElement | null) => {
    // Clean up previous element's listeners
    if (cleanupRef.current) {
      cleanupRef.current()
      cleanupRef.current = null
    }

    audioRef.current = element

    if (!element) {
      setCurrentTime(0)
      setIsPlaying(false)
      setDuration(0)
      return
    }

    // Set up event listeners
    const handleTimeUpdate = () => setCurrentTime(element.currentTime)
    const handlePlay = () => setIsPlaying(true)
    const handlePause = () => setIsPlaying(false)
    const handleLoadedMetadata = () => setDuration(element.duration)
    const handleDurationChange = () => setDuration(element.duration)

    element.addEventListener('timeupdate', handleTimeUpdate)
    element.addEventListener('play', handlePlay)
    element.addEventListener('pause', handlePause)
    element.addEventListener('loadedmetadata', handleLoadedMetadata)
    element.addEventListener('durationchange', handleDurationChange)

    // Initialize duration if already loaded
    if (element.duration) {
      setDuration(element.duration)
    }

    // Store cleanup function
    cleanupRef.current = () => {
      element.removeEventListener('timeupdate', handleTimeUpdate)
      element.removeEventListener('play', handlePlay)
      element.removeEventListener('pause', handlePause)
      element.removeEventListener('loadedmetadata', handleLoadedMetadata)
      element.removeEventListener('durationchange', handleDurationChange)
    }
  }, [])

  const seekTo = useCallback((timeInSeconds: number) => {
    const audio = audioRef.current
    if (!audio) return

    // Clamp time to valid range
    const clampedTime = Math.max(0, Math.min(timeInSeconds, audio.duration || Infinity))
    audio.currentTime = clampedTime

    // Start playing if not already
    if (audio.paused) {
      audio.play().catch(() => {
        // Ignore autoplay errors (browser policy)
      })
    }
  }, [])

  return (
    <AudioPlayerContext.Provider
      value={{
        audioRef,
        currentTime,
        isPlaying,
        duration,
        seekTo,
        registerAudio,
      }}
    >
      {children}
    </AudioPlayerContext.Provider>
  )
}

export function useAudioPlayer() {
  const context = useContext(AudioPlayerContext)
  if (!context) {
    throw new Error('useAudioPlayer must be used within an AudioPlayerProvider')
  }
  return context
}

// Optional hook that returns null instead of throwing when outside provider
// Useful for components that may or may not have player functionality
export function useAudioPlayerOptional() {
  return useContext(AudioPlayerContext)
}
