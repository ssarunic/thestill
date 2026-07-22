import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePlayer } from '../contexts/PlayerContext'

// Spec #61 §2 — desktop-only surface; on mobile playback continues
// audio-first in the mini-player bar. Falls back to "desktop" when
// matchMedia is unavailable (tests).
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === 'undefined' || typeof window.matchMedia !== 'function'
      ? true
      : window.matchMedia('(min-width: 640px)').matches
  )
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia('(min-width: 640px)')
    const onChange = () => setIsDesktop(mql.matches)
    mql.addEventListener?.('change', onChange)
    return () => mql.removeEventListener?.('change', onChange)
  }, [])
  return isDesktop
}

/**
 * Spec #61 §2 — floating video tile. Shown when no theater slot is
 * registered (user navigated away from the reader) and video is enabled:
 * a draggable ~320 px tile bottom-right, above the mini-player bar, with
 * close (drops to audio-first) and expand (back to the episode)
 * affordances. Renders only the chrome — the video area is a registered
 * slot the global media layer positions the stable <video> node over.
 *
 * Mounted by App outside the overlay pass; while the reader overlay is
 * open App unmounts the tile, so no floating surface fights the overlay's
 * focus trap or vanishes behind its scrim (§3, §12).
 */
export default function FloatingVideoTile() {
  const player = usePlayer()
  const navigate = useNavigate()
  const isDesktop = useIsDesktop()
  const videoAreaRef = useRef<HTMLDivElement>(null)
  const { registerFloatingSlot, setVideoPreference, presentation, track, pipSupported, requestPip } = player

  const show = presentation === 'floating' && isDesktop

  const [offset, setOffset] = useState({ dx: 0, dy: 0 })
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; baseDx: number; baseDy: number } | null>(null)

  useEffect(() => {
    const el = videoAreaRef.current
    if (!show || !el) return
    return registerFloatingSlot(el)
  }, [show, registerFloatingSlot])

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Buttons in the header keep their click behavior — no drag.
      if ((e.target as HTMLElement).closest('button')) return
      dragRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        baseDx: offset.dx,
        baseDy: offset.dy,
      }
      e.currentTarget.setPointerCapture(e.pointerId)
    },
    [offset]
  )

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    setOffset({ dx: drag.baseDx + (e.clientX - drag.startX), dy: drag.baseDy + (e.clientY - drag.startY) })
  }, [])

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === e.pointerId) dragRef.current = null
  }, [])

  if (!show || !track) return null

  const episodePath = `/podcasts/${track.podcastSlug}/episodes/${track.episodeSlug}`

  return (
    <div
      role="complementary"
      aria-label="Floating video player"
      data-testid="floating-video-tile"
      className="fixed bottom-24 right-4 z-40 w-80 overflow-hidden rounded-lg border border-gray-200 bg-gray-900 shadow-xl"
      style={{ transform: `translate(${offset.dx}px, ${offset.dy}px)` }}
    >
      <div
        className="flex cursor-move touch-none items-center gap-1 px-2 py-1.5"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <p className="min-w-0 flex-1 truncate text-xs font-medium text-white" title={track.title}>
          {track.title}
        </p>
        {pipSupported && (
          <button
            type="button"
            onClick={requestPip}
            aria-label="Picture-in-picture"
            className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded text-gray-300 hover:bg-white/10 hover:text-white"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3" y="5" width="18" height="14" rx="2" />
              <rect x="12" y="12" width="7" height="5" rx="1" fill="currentColor" stroke="none" />
            </svg>
          </button>
        )}
        <button
          type="button"
          onClick={() => navigate(episodePath)}
          aria-label="Expand to episode"
          className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded text-gray-300 hover:bg-white/10 hover:text-white"
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l6 6m10-2V4m0 0h-4m4 0l-6 6M4 16v4m0 0h4m-4 0l6-6m10 2v4m0 0h-4m4 0l-6-6" />
          </svg>
        </button>
        <button
          type="button"
          onClick={() => setVideoPreference('audio-only')}
          aria-label="Close video (keep listening)"
          className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded text-gray-300 hover:bg-white/10 hover:text-white"
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div ref={videoAreaRef} data-testid="floating-video-slot" className="aspect-video w-full bg-black" />
    </div>
  )
}
