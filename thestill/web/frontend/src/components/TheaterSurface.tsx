import { useCallback, useEffect, useRef } from 'react'
import { usePlayer, type PlayerTrack } from '../contexts/PlayerContext'

/**
 * Spec #61 §2 — theater presentation surface. For video episodes the
 * reader registers a 16:9 slot above the transcript; the global media
 * layer positions the one stable <video> node over it. This component
 * renders only the shell (poster, activation button, surface controls) —
 * never a media element of its own (invariant 1: one active engine).
 *
 * Registration is by mounted surface, not pathname (§3): the reader
 * renders both as a standalone page and inside the z-50 overlay, and the
 * media layer resolves z-index from the registered slot's DOM position.
 *
 * On mobile the slot sits sticky at the top of the episode page and the
 * transcript stays the primary surface (YouTube-mobile pattern).
 */
interface TheaterSurfaceProps {
  episodeId: string
  posterUrl?: string | null
  // Track to start when the user activates playback from the poster.
  track: PlayerTrack
}

export default function TheaterSurface({ episodeId, posterUrl, track }: TheaterSurfaceProps) {
  const player = usePlayer()
  const slotRef = useRef<HTMLDivElement>(null)
  const {
    registerTheaterSlot,
    setVideoPreference,
    switchRendition,
    videoPreference,
    activeRendition,
    canSwitchRendition,
    mediaKind,
    pipSupported,
    pipActive,
    requestPip,
    mediaError,
  } = player

  const isCurrent = player.isCurrent(episodeId)
  // The slot is "compatible" (§3) only while this episode is the session's
  // track, the session is on the video rendition, and the user hasn't hidden
  // video. Not registering is how "Hide video" works on the reader — the
  // presentation machine then falls through to hidden/audio-first.
  const slotActive =
    isCurrent && mediaKind === 'video' && activeRendition === 'video' && videoPreference === 'shown'

  useEffect(() => {
    const el = slotRef.current
    if (!slotActive || !el) return
    return registerTheaterSlot(episodeId, el)
  }, [slotActive, episodeId, registerTheaterSlot])

  const activate = useCallback(() => {
    setVideoPreference('shown')
    if (!isCurrent) {
      player.play(track)
      return
    }
    if (activeRendition !== 'video') switchRendition('video')
    if (!player.isPlaying) player.resume()
  }, [setVideoPreference, isCurrent, player, track, activeRendition, switchRendition])

  return (
    <div className="space-y-2">
      <div className="sticky top-0 z-20 lg:static lg:z-auto">
        <div
          ref={slotRef}
          data-testid="theater-slot"
          className="relative aspect-video w-full overflow-hidden rounded-lg bg-black shadow-sm"
        >
          {posterUrl ? (
            <img
              src={posterUrl}
              alt=""
              className="absolute inset-0 h-full w-full object-cover"
            />
          ) : null}
          {!slotActive && (
            <button
              type="button"
              onClick={activate}
              aria-label={isCurrent ? 'Show video' : 'Play video'}
              className="absolute inset-0 flex items-center justify-center bg-black/40 transition-colors hover:bg-black/30"
            >
              <span className="flex h-16 w-16 items-center justify-center rounded-full bg-white/90 text-gray-900 shadow-lg">
                <svg className="ml-1 h-8 w-8" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </span>
            </button>
          )}
          {pipActive && slotActive && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/70">
              <p className="text-sm font-medium text-white">Playing in picture-in-picture</p>
            </div>
          )}
        </div>
      </div>

      {isCurrent && mediaError && (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {mediaError}
        </p>
      )}

      {isCurrent && (
        <div className="flex flex-wrap items-center justify-end gap-2 text-xs font-medium">
          {pipSupported && slotActive && (
            <button
              type="button"
              onClick={requestPip}
              className="rounded-md border border-gray-200 px-2.5 py-1.5 text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            >
              {pipActive ? 'Exit picture-in-picture' : 'Picture-in-picture'}
            </button>
          )}
          {/* Spec #61 §5 — visual-off toggle: same resource, instant. The
              re-show affordance is the poster overlay button, so only the
              hide direction lives here. */}
          {videoPreference === 'shown' && (
            <button
              type="button"
              onClick={() => setVideoPreference('audio-only')}
              className="rounded-md border border-gray-200 px-2.5 py-1.5 text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            >
              Hide video
            </button>
          )}
          {/* Spec #61 §5 — real rendition switch: saves data/battery. Only
              offered when the manifest carries both renditions. */}
          {canSwitchRendition && (
            <button
              type="button"
              onClick={() => switchRendition(activeRendition === 'video' ? 'audio' : 'video')}
              className="rounded-md border border-gray-200 px-2.5 py-1.5 text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            >
              {activeRendition === 'video' ? 'Use audio rendition' : 'Use video rendition'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
