import { usePlayer, type PlayerTrack } from '../contexts/PlayerContext'

interface AudioPlayerProps {
  track: PlayerTrack
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function AudioPlayer({ track }: AudioPlayerProps) {
  const player = usePlayer()
  const isCurrent = player.isCurrent(track.episodeId)
  const isPlaying = isCurrent && player.isPlaying
  const isLoading = isCurrent && player.isLoading
  const currentTime = isCurrent ? player.currentTime : 0
  const duration = isCurrent && player.duration > 0
    ? player.duration
    : track.durationHint ?? 0
  const hasDuration = duration > 0 && Number.isFinite(duration)
  const progress = hasDuration ? Math.min(1, currentTime / duration) : 0

  const onPlayPause = () => {
    if (isCurrent) {
      player.toggle()
    } else {
      player.play(track)
    }
  }

  return (
    <div
      className="bg-gray-100 rounded-lg p-3 sm:p-4"
      role="region"
      aria-label="Episode player"
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onPlayPause}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="w-12 h-12 flex items-center justify-center rounded-full bg-primary-900 text-white hover:bg-primary-800 active:bg-primary-700 flex-shrink-0 disabled:opacity-50"
          disabled={isLoading && !isPlaying}
        >
          {isLoading && !isPlaying ? (
            <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
          ) : isPlaying ? (
            <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg className="w-6 h-6 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-xs text-gray-600 tabular-nums">
            <span>{formatTime(currentTime)}</span>
            <input
              type="range"
              min={0}
              max={hasDuration ? duration : 100}
              step={0.1}
              value={hasDuration ? currentTime : 0}
              onChange={(e) => {
                if (!isCurrent) player.play(track)
                player.seek(Number(e.target.value))
              }}
              disabled={!hasDuration}
              aria-label="Seek"
              className="flex-1 h-1 appearance-none bg-gray-200 rounded cursor-pointer disabled:cursor-not-allowed"
              style={{
                background: `linear-gradient(to right, #486581 ${progress * 100}%, #e5e7eb ${progress * 100}%)`,
              }}
            />
            <span>{hasDuration ? formatTime(duration) : '--:--'}</span>
          </div>
          <p className="text-xs text-gray-500 mt-2 truncate">{track.title}</p>
        </div>

        <a
          href={track.audioUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="hidden sm:flex items-center justify-center text-gray-500 hover:text-primary-700 w-9 h-9 rounded-full hover:bg-gray-200 flex-shrink-0"
          title="Open audio in new tab"
          aria-label="Open audio in new tab"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      </div>
    </div>
  )
}
