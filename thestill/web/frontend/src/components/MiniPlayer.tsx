import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import { useBackgroundLocation } from '../hooks/useBackgroundLocation'
import { PLAYER_HEIGHT_VAR } from '../constants/layers'

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

// Elements that own the space bar themselves: typing, native activation,
// slider nudging. Anywhere else, space toggles playback (spec #71 §4).
const SPACE_OWNER_SELECTOR =
  'input, textarea, select, button, [contenteditable=""], [contenteditable="true"], [role="slider"], iframe'

/**
 * Spec #22 — persistent transport. Spec #71 — the bar is shell chrome: it
 * sits above the reader overlay (z-50 vs z-[45]) and publishes its rendered
 * height as `--player-h` on the document root so the overlay, page padding
 * and every bottom-anchored pill can inset above it instead of being
 * covered by it.
 */
export default function MiniPlayer() {
  const {
    track,
    isPlaying,
    isLoading,
    duration,
    toggle,
    seek,
    skip,
    stop,
    mediaKind,
    youtubeAvailable,
    setVideoPreference,
  } = usePlayer()
  const currentTime = usePlayerTime()
  const location = useLocation()
  const backgroundLocation = useBackgroundLocation()
  const barRef = useRef<HTMLDivElement>(null)
  const hasTrack = track != null

  // Publish the bar's height (0 when hidden). ResizeObserver covers the
  // sm/lg padding changes and safe-area insets; the resize fallback is for
  // environments without it (jsdom).
  useEffect(() => {
    const root = document.documentElement
    const el = barRef.current
    if (!hasTrack || !el) {
      root.style.setProperty(PLAYER_HEIGHT_VAR, '0px')
      return
    }
    const update = () => root.style.setProperty(PLAYER_HEIGHT_VAR, `${el.offsetHeight}px`)
    update()
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    observer?.observe(el)
    if (!observer) window.addEventListener('resize', update)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
      root.style.setProperty(PLAYER_HEIGHT_VAR, '0px')
    }
  }, [hasTrack])

  // Space toggles playback anywhere a track is loaded, mirroring the Media
  // Session play/pause handlers for the keyboard. Ignored while typing or
  // when a control that activates on space has focus.
  useEffect(() => {
    if (!hasTrack) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== ' ' || e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return
      const target = e.target
      if (target instanceof Element && target.closest(SPACE_OWNER_SELECTOR)) return
      e.preventDefault()
      toggle()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [hasTrack, toggle])

  if (!track) return null

  const hasDuration = duration > 0 && Number.isFinite(duration)
  const progress = hasDuration ? Math.min(1, currentTime / duration) : 0
  const episodePath = `/podcasts/${track.podcastSlug}/episodes/${track.episodeSlug}`

  // Links into the episode keep the inbox's overlay contract (spec #52):
  // from the inbox, or from inside an overlay already open over it, the
  // reader opens above the still-mounted list. Elsewhere it is a plain
  // navigation to the standalone page, as before.
  const inInbox = location.pathname === '/inbox' || location.pathname.startsWith('/inbox/')
  const linkState = backgroundLocation
    ? { backgroundLocation }
    : inInbox
      ? { backgroundLocation: location }
      : undefined
  const alreadyOnEpisode = location.pathname === episodePath

  return (
    <div
      ref={barRef}
      role="region"
      aria-label="Audio player"
      className="fixed bottom-0 left-0 right-0 sm:left-16 lg:left-64 z-50 bg-white border-t border-gray-200 shadow-lg pb-[env(safe-area-inset-bottom)]"
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

      <div className="flex items-center gap-2 px-3 py-2 sm:gap-3 sm:px-4 sm:py-3">
        {track.artworkUrl ? (
          <img
            src={track.artworkUrl}
            alt=""
            width={40}
            height={40}
            className="w-9 h-9 sm:w-10 sm:h-10 rounded object-cover flex-shrink-0"
          />
        ) : null}

        <div className="flex-1 min-w-0">
          <Link
            to={episodePath}
            state={linkState}
            onClick={(e) => {
              if (alreadyOnEpisode) e.preventDefault()
            }}
            className="block text-sm font-medium text-gray-900 truncate hover:underline"
            title={track.title}
          >
            {track.title}
          </Link>
          {track.podcastTitle ? (
            <p className="text-xs text-gray-500 truncate">{track.podcastTitle}</p>
          ) : null}
        </div>

        {/* Spec #61 §2 — for video episodes the mini player keeps episode
            artwork (no live thumbnail: one DOM video cannot render in two
            places) and gains a "Show video" affordance. Spec #62 — tracks
            with a YouTube link get the same affordance; it leads back to
            the reader, where the "Watch video" opt-in re-enters the
            YouTube rendition (the iframe never plays off-surface). */}
        {(mediaKind === 'video' || youtubeAvailable) && (
          <Link
            to={episodePath}
            state={linkState}
            onClick={() => setVideoPreference('shown')}
            aria-label="Show video"
            title="Show video"
            className="hidden sm:flex w-9 h-9 items-center justify-center rounded-full text-gray-600 hover:bg-gray-100 hover:text-gray-900 flex-shrink-0"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
              <rect x="2" y="6" width="14" height="12" rx="2" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M22 9l-6 3 6 3V9z" />
            </svg>
          </Link>
        )}

        <div className="hidden sm:flex items-center gap-1 text-xs text-gray-500 tabular-nums min-w-[90px] justify-end">
          <span>{formatTime(currentTime)}</span>
          <span>/</span>
          <span>{hasDuration ? formatTime(duration) : '--:--'}</span>
        </div>

        {/* Transport — the three actions every mobile bar surveyed carries
            (spec #71 §3). 44 px targets below sm, the previous 36/40 px above. */}
        <div className="flex items-center gap-0.5 sm:gap-1 flex-shrink-0">
          <button
            type="button"
            onClick={() => skip(-15)}
            aria-label="Back 15 seconds"
            disabled={!hasDuration}
            className="flex w-11 h-11 sm:w-9 sm:h-9 items-center justify-center rounded-full text-gray-600 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24">
              <path d="M11 17l-5-5 5-5" />
              <path d="M18 17l-5-5 5-5" />
            </svg>
            <span className="sr-only">15</span>
          </button>

          <button
            type="button"
            onClick={toggle}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            className="w-11 h-11 sm:w-10 sm:h-10 flex items-center justify-center rounded-full bg-primary-900 text-white hover:bg-primary-800 active:bg-primary-700 disabled:opacity-50"
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
            onClick={() => skip(15)}
            aria-label="Forward 15 seconds"
            disabled={!hasDuration}
            className="flex w-11 h-11 sm:w-9 sm:h-9 items-center justify-center rounded-full text-gray-600 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24">
              <path d="M13 17l5-5-5-5" />
              <path d="M6 17l5-5-5-5" />
            </svg>
            <span className="sr-only">15</span>
          </button>
        </div>

        {/* Stop-and-dismiss is destructive next to Play; on phones it moves
            into the expanded Now Playing sheet (spec #72). */}
        <button
          type="button"
          onClick={stop}
          aria-label="Close player"
          className="hidden sm:flex w-9 h-9 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex-shrink-0"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  )
}
