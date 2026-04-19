import { Link } from 'react-router-dom'
import { usePlayer } from '../contexts/PlayerContext'

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

export default function MiniPlayer() {
  const {
    track,
    isPlaying,
    isLoading,
    currentTime,
    duration,
    toggle,
    seek,
    stop,
  } = usePlayer()

  if (!track) return null

  const hasDuration = duration > 0 && Number.isFinite(duration)
  const progress = hasDuration ? Math.min(1, currentTime / duration) : 0
  const episodePath = `/podcasts/${track.podcastSlug}/episodes/${track.episodeSlug}`

  return (
    <div
      role="region"
      aria-label="Audio player"
      className="fixed bottom-0 left-0 right-0 sm:left-16 lg:left-64 z-30 bg-white border-t border-gray-200 shadow-lg"
    >
      <div className="relative">
        <input
          type="range"
          min={0}
          max={hasDuration ? duration : 100}
          step={0.1}
          value={hasDuration ? currentTime : 0}
          onChange={(e) => seek(Number(e.target.value))}
          disabled={!hasDuration}
          aria-label="Seek"
          className="absolute top-0 left-0 right-0 w-full h-1 appearance-none bg-gray-200 cursor-pointer disabled:cursor-not-allowed accent-primary-600"
          style={{
            background: `linear-gradient(to right, #486581 ${progress * 100}%, #e5e7eb ${progress * 100}%)`,
          }}
        />
      </div>

      <div className="flex items-center gap-3 px-3 py-2 sm:px-4 sm:py-3">
        {track.artworkUrl ? (
          <img
            src={track.artworkUrl}
            alt=""
            width={40}
            height={40}
            className="w-10 h-10 rounded object-cover flex-shrink-0 hidden sm:block"
          />
        ) : null}

        <div className="flex-1 min-w-0">
          <Link
            to={episodePath}
            className="block text-sm font-medium text-gray-900 truncate hover:underline"
            title={track.title}
          >
            {track.title}
          </Link>
          {track.podcastTitle ? (
            <p className="text-xs text-gray-500 truncate">{track.podcastTitle}</p>
          ) : null}
        </div>

        <div className="hidden sm:flex items-center gap-1 text-xs text-gray-500 tabular-nums min-w-[90px] justify-end">
          <span>{formatTime(currentTime)}</span>
          <span>/</span>
          <span>{hasDuration ? formatTime(duration) : '--:--'}</span>
        </div>

        <button
          type="button"
          onClick={toggle}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="w-10 h-10 flex items-center justify-center rounded-full bg-primary-900 text-white hover:bg-primary-800 active:bg-primary-700 flex-shrink-0 disabled:opacity-50"
          disabled={isLoading && !isPlaying}
        >
          {isLoading && !isPlaying ? (
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
          ) : isPlaying ? (
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg className="w-5 h-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>

        <button
          type="button"
          onClick={stop}
          aria-label="Close player"
          className="w-9 h-9 flex items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex-shrink-0"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  )
}
