import { useEffect, useRef } from 'react'
import { useAudioPlayerOptional } from '../contexts/AudioPlayerContext'

interface AudioPlayerProps {
  audioUrl: string
  title: string
}

export default function AudioPlayer({ audioUrl, title }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const playerContext = useAudioPlayerOptional()

  // Register the audio element with the context when available
  useEffect(() => {
    if (playerContext && audioRef.current) {
      playerContext.registerAudio(audioRef.current)
      return () => {
        playerContext.registerAudio(null)
      }
    }
  }, [playerContext, audioUrl])

  return (
    <div className="bg-gray-100 rounded-lg p-3 sm:p-4">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
        {/* Play button / audio element */}
        <audio
          ref={audioRef}
          controls
          className="w-full sm:flex-1 h-10"
          src={audioUrl}
          preload="none"
        >
          <a href={audioUrl} target="_blank" rel="noopener noreferrer">
            Listen to episode
          </a>
        </audio>

        {/* External link */}
        <a
          href={audioUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1 text-sm text-primary-600 hover:text-primary-700 py-2 sm:py-0"
          title="Open in new tab"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
          <span>Open in new tab</span>
        </a>
      </div>
      <p className="text-xs text-gray-500 mt-2 truncate">{title}</p>
    </div>
  )
}
