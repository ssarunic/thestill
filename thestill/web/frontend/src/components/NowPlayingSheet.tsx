import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import { useIsSmUp } from '../hooks/useMediaQuery'
import { useEpisodeLinkState } from '../hooks/useEpisodeLinkState'
import { useEpisodeEntities } from '../hooks/useApi'
import { abovePlayer, MEDIA_HOST_ATTR } from '../constants/layers'
import { selectTopEntities } from '../utils/mentionDensity'
import { entityStyle } from '../utils/entityColors'
import { formatClock } from '../utils/formatClock'
import Artwork from './Artwork'
import Button, { CloseIcon, PauseIcon, PlayIcon } from './Button'
import NowPlayingScrubber, { type ScrubberTick } from './NowPlayingScrubber'
import NowPlayingSpeedControl from './NowPlayingSpeedControl'
import NowPlayingKaraokeLine from './NowPlayingKaraokeLine'

interface NowPlayingSheetProps {
  isOpen: boolean
  onClose: () => void
}

// Mirrors EpisodeReaderOverlay's trap: close enough to the browser's notion
// of tabbable for this panel's controls.
const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Swipe-down on the phone sheet's drag handle closes past this travel.
const SWIPE_CLOSE_PX = 80

// Material "replay" / "forward" rings (Apache-2.0) with the skip length set
// inside, so the buttons read as ±15 s without a caption. Rendered as button
// children rather than through ``icon`` so the glyph can be 32 px — the
// ``icon`` wrapper's 20 px is too small for the digits.
const SkipBackIcon = () => (
  <svg fill="currentColor" viewBox="0 0 24 24" className="h-8 w-8" aria-hidden="true">
    <path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z" />
    <text x="12" y="15.6" textAnchor="middle" fontSize="7" fontWeight="700">
      15
    </text>
  </svg>
)

const SkipForwardIcon = () => (
  <svg fill="currentColor" viewBox="0 0 24 24" className="h-8 w-8" aria-hidden="true">
    <path d="M12 5V1l5 5-5 5V7c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6h2c0 4.42-3.58 8-8 8s-8-3.58-8-8 3.58-8 8-8z" />
    <text x="12" y="15.6" textAnchor="middle" fontSize="7" fontWeight="700">
      15
    </text>
  </svg>
)

const VolumeIcon = ({ muted }: { muted: boolean }) => (
  <svg fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path d="M11 5L6 9H3v6h3l5 4V5z" />
    {muted ? <path d="M22 9l-6 6M16 9l6 6" /> : <path d="M15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13" />}
  </svg>
)

const TranscriptIcon = () => (
  <svg fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" className="h-6 w-6" aria-hidden="true">
    <path d="M7 4h7l5 5v11a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1z" />
    <path d="M14 4v5h5M9 13h6M9 17h6" />
  </svg>
)

const VideoIcon = () => (
  <svg fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" className="h-6 w-6" aria-hidden="true">
    <rect x="3" y="6" width="13" height="12" rx="2" />
    <path d="M16 10l5-3v10l-5-3z" />
  </svg>
)

const PipIcon = () => (
  <svg fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} viewBox="0 0 24 24" className="h-6 w-6" aria-hidden="true">
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <rect x="11" y="11" width="8" height="6" rx="1" fill="currentColor" stroke="none" />
  </svg>
)

// One slot of the utility row (spec #72 §6): a 24 px glyph over an 11 px
// label, 44 px+ tall, ghost at rest and tinted when the action is "on".
function utilityClass(on = false): string {
  return [
    'flex min-h-[52px] min-w-[64px] flex-col items-center justify-center gap-1 rounded-lg px-2 py-1.5 transition-colors',
    on ? 'bg-primary-50 text-primary-900' : 'text-gray-600 hover:bg-gray-100 hover:text-ink active:bg-gray-200',
  ].join(' ')
}

/**
 * Spec #72 — the expanded Now Playing surface. One component, two forms
 * chosen by `useIsSmUp`: a bottom sheet with scrim, focus trap and swipe-down
 * below `sm`; a card anchored above the mini player's left edge from `sm`,
 * closed by Esc or a click outside. Both sit on the transient rung
 * (`z-[70]`, spec #71) and own no playback state — they read the player
 * context exactly as the bar does. Closing never touches playback.
 */
export default function NowPlayingSheet({ isOpen, onClose }: NowPlayingSheetProps) {
  const player = usePlayer()
  const currentTime = usePlayerTime()
  const isSmUp = useIsSmUp()
  const { track } = player
  const episodePath = track ? `/podcasts/${track.podcastSlug}/episodes/${track.episodeSlug}` : ''
  const { state: linkState, alreadyHere } = useEpisodeLinkState(episodePath)
  const panelRef = useRef<HTMLDivElement>(null)
  const isPhone = !isSmUp
  const active = isOpen && track !== null

  // Spec #72 2c — on a phone there is no floating tile, so when the session
  // has a visual rendition and nothing presents it, the sheet's header hosts
  // the video: it registers a theater slot (spec #61 §3) and the media layer
  // positions the stable node over it, one rung above the sheet (layers.ts).
  // It yields to a slot the reader already holds — the reader is the primary
  // surface — and unregisters on close, at which point the existing #62 §7
  // effect drops a YouTube session to audio exactly as leaving the reader does.
  const videoPresentable =
    track !== null && ((player.mediaKind === 'video' && player.activeRendition === 'video') || player.activeEngine === 'youtube')
  const hostVideo = active && isPhone && videoPresentable && player.videoPreference === 'shown'
  const videoSlotRef = useRef<HTMLDivElement>(null)
  const episodeId = track?.episodeId
  const { registerTheaterSlot, hasTheaterSlot } = player
  useEffect(() => {
    const el = videoSlotRef.current
    if (!hostVideo || !el || !episodeId) return
    if (hasTheaterSlot()) return
    return registerTheaterSlot(episodeId, el)
  }, [hostVideo, episodeId, hasTheaterSlot, registerTheaterSlot])

  // Spec #72 §2 entity ticks — the #28 §5.2 density timeline's home. Same
  // query key as the reader, so the cache entry is shared; fetched only
  // while the sheet is open.
  const { data: entitiesData } = useEpisodeEntities(active ? track.episodeId : null)
  const duration = player.duration
  const seek = player.seek
  const ticks = useMemo<ScrubberTick[]>(() => {
    if (!(duration > 0) || !entitiesData?.entities) return []
    return selectTopEntities(entitiesData.entities).flatMap((item) =>
      item.mentions.map((m) => {
        const seconds = m.start_ms / 1000
        return {
          at: seconds / duration,
          colorClass: entityStyle(item.entity.type).dot,
          label: `${item.entity.canonical_name} at ${formatClock(seconds)}`,
          onSelect: () => seek(seconds),
        }
      }),
    )
  }, [duration, entitiesData, seek])

  // Esc closes unless a surface layered above owns the key (⌘K's input) —
  // same two guards as the reader overlay.
  useEffect(() => {
    if (!active) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      const focused = document.activeElement
      if (focused && focused !== document.body && !panelRef.current?.contains(focused)) return
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [active, onClose])

  // Desktop card: click outside closes. The bar's expand button toggles by
  // itself, so a mousedown on it is left alone (otherwise the card would
  // close on mousedown and reopen on click).
  useEffect(() => {
    if (!active || isPhone) return
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target
      if (!(target instanceof Element)) return
      if (panelRef.current?.contains(target)) return
      if (target.closest('[aria-haspopup="dialog"][aria-expanded="true"]')) return
      onClose()
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [active, isPhone, onClose])

  // Phone sheet: lock the page behind it.
  useEffect(() => {
    if (!active || !isPhone) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [active, isPhone])

  // Focus moves into the panel on open and back to the opener on close.
  useEffect(() => {
    if (!active) return
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panelRef.current?.focus()
    return () => origin?.focus()
  }, [active])

  const trapFocus = useCallback(
    (e: React.KeyboardEvent) => {
      if (!isPhone || e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [isPhone],
  )

  // Swipe-down on the drag handle (phone).
  const [dragY, setDragY] = useState(0)
  const dragRef = useRef<{ pointerId: number; startY: number } | null>(null)
  const onHandlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = { pointerId: e.pointerId, startY: e.clientY }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onHandlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    setDragY(Math.max(0, e.clientY - drag.startY))
  }
  const onHandlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    dragRef.current = null
    const travelled = e.clientY - drag.startY
    setDragY(0)
    if (travelled >= SWIPE_CLOSE_PX) onClose()
  }

  if (!active || !track) return null

  const { isPlaying, isLoading, playbackRate, availableRates, mediaError, volume, muted, videoPreference, pipSupported, pipActive } = player
  const hasVisualRendition = player.mediaKind === 'video' || player.activeEngine === 'youtube'
  const hasDuration = duration > 0 && Number.isFinite(duration)
  const busy = isLoading && !isPlaying

  const panel = (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal={isPhone || undefined}
      aria-label="Now playing"
      tabIndex={-1}
      onKeyDown={trapFocus}
      data-testid="now-playing-sheet"
      {...{ [MEDIA_HOST_ATTR]: 'now-playing' }}
      style={isPhone && dragY ? { transform: `translateY(${dragY}px)` } : undefined}
      className={
        isPhone
          ? 'absolute inset-x-0 bottom-0 flex max-h-[92vh] flex-col rounded-t-2xl bg-surface shadow-xl outline-none pb-[env(safe-area-inset-bottom)]'
          : 'w-[26rem] max-w-[calc(100vw-2rem)] rounded-xl border border-hairline bg-surface shadow-xl outline-none'
      }
    >
      {isPhone && (
        <div
          className="flex cursor-grab touch-none justify-center py-2 active:cursor-grabbing"
          onPointerDown={onHandlePointerDown}
          onPointerMove={onHandlePointerMove}
          onPointerUp={onHandlePointerUp}
          onPointerCancel={onHandlePointerUp}
          data-testid="now-playing-drag-handle"
          aria-hidden="true"
        >
          <span className="h-1.5 w-10 rounded-full bg-gray-300" />
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-5 pb-4 pt-1 sm:pt-4">
        {hostVideo && (
          <div
            ref={videoSlotRef}
            data-testid="now-playing-video-slot"
            className="mb-4 aspect-video w-full overflow-hidden rounded-lg bg-black"
          />
        )}

        {/* Header. The close button top-aligns; artwork and titles centre on
            each other so a two-line title sits level with the artwork. */}
        <div className="flex items-start gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-4">
            {!hostVideo && <Artwork role={isPhone ? 'card' : 'sheet'} sources={[track.artworkUrl]} loading="eager" />}
            <div className="min-w-0 flex-1">
              <Link
                to={episodePath}
                state={linkState}
                onClick={(e) => {
                  if (alreadyHere) {
                    e.preventDefault()
                    onClose()
                  }
                }}
                className="line-clamp-2 text-base font-semibold leading-snug text-ink hover:underline"
              >
                {track.title}
              </Link>
              {track.podcastTitle ? <p className="mt-1 truncate text-sm text-muted">{track.podcastTitle}</p> : null}
            </div>
          </div>
          <Button size="icon" variant="ghost" icon={<CloseIcon />} onClick={onClose} aria-label="Close now playing" className="-mr-2">
            <span className="sr-only">Close</span>
          </Button>
        </div>

        {mediaError && (
          <p role="alert" className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {mediaError}
          </p>
        )}

        {/* Scrubber */}
        <div className="mt-5">
          <NowPlayingScrubber currentTime={currentTime} duration={duration} onSeek={player.seek} ticks={ticks} />
        </div>

        {/* Current line (spec #72 §3) — what is being said right now, with the
            #38 wipe. Reserved at two lines so the transport below does not
            jump as the segment changes length or drops out. Rendered only
            while the sheet is open (data fetch is gated on `active`). */}
        <div className="mt-3 min-h-[2.875rem] text-center">
          <NowPlayingKaraokeLine track={track} enabled={active} />
        </div>

        {/* Transport */}
        <div className="mt-3 flex items-center justify-center gap-8">
          <Button
            size="icon"
            variant="ghost"
            onClick={() => player.skip(-15)}
            disabled={!hasDuration}
            aria-label="Back 15 seconds"
          >
            <SkipBackIcon />
            <span className="sr-only">Back 15 seconds</span>
          </Button>
          <Button
            size="iconLg"
            variant="primary"
            icon={isPlaying ? <PauseIcon /> : <PlayIcon />}
            isLoading={busy}
            onClick={player.toggle}
            aria-label={isPlaying ? 'Pause' : 'Play'}
          >
            <span className="sr-only">{isPlaying ? 'Pause' : 'Play'}</span>
          </Button>
          <Button
            size="icon"
            variant="ghost"
            onClick={() => player.skip(15)}
            disabled={!hasDuration}
            aria-label="Forward 15 seconds"
          >
            <SkipForwardIcon />
            <span className="sr-only">Forward 15 seconds</span>
          </Button>
        </div>

        {/* Utility row (spec #72 §5–6) — the small stuff, evenly spaced and
            secondary in weight: speed steps on tap; Transcript is the one
            way out to the reader; video and PiP appear only when a visual
            rendition exists. Stop is not here: Pause is the way to stop, the
            desktop bar keeps its ✕, and the bar otherwise persists like any
            player's. Follow-playback lives with the transcript, where its
            effect is visible. */}
        <div className="mt-4 flex items-start justify-evenly">
          <NowPlayingSpeedControl
            rate={playbackRate}
            availableRates={availableRates}
            onChange={player.setRate}
            className={utilityClass()}
          />
          <Link
            to={{ pathname: episodePath, search: `?view=transcript&t=${Math.floor(currentTime)}` }}
            state={linkState}
            onClick={onClose}
            aria-label="Open transcript here"
            className={utilityClass()}
          >
            <TranscriptIcon />
            <span aria-hidden="true" className="text-[11px] font-medium leading-none">
              Transcript
            </span>
          </Link>
          {hasVisualRendition && (
            <button
              type="button"
              onClick={() => player.setVideoPreference(videoPreference === 'shown' ? 'audio-only' : 'shown')}
              aria-pressed={videoPreference === 'shown'}
              aria-label={videoPreference === 'shown' ? 'Hide video' : 'Show video'}
              className={utilityClass(videoPreference === 'shown')}
            >
              <VideoIcon />
              <span aria-hidden="true" className="text-[11px] font-medium leading-none">
                Video
              </span>
            </button>
          )}
          {pipSupported && videoPresentable && (
            <button
              type="button"
              onClick={player.requestPip}
              aria-pressed={pipActive}
              aria-label={pipActive ? 'Exit picture-in-picture' : 'Picture-in-picture'}
              className={utilityClass(pipActive)}
            >
              <PipIcon />
              <span aria-hidden="true" className="text-[11px] font-medium leading-none">
                Pop out
              </span>
            </button>
          )}
        </div>

        {/* Volume — pointer devices only; phones use the hardware rocker. */}
        {!isPhone && (
          <div className="mt-2 flex items-center gap-2">
            <Button
              size="icon"
              variant="ghost"
              icon={<VolumeIcon muted={muted} />}
              onClick={player.toggleMute}
              aria-label={muted ? 'Unmute' : 'Mute'}
              aria-pressed={muted}
            >
              <span className="sr-only">{muted ? 'Unmute' : 'Mute'}</span>
            </Button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={muted ? 0 : volume}
              onChange={(e) => player.setVolume(Number(e.target.value))}
              aria-label="Volume"
              className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-gray-200 accent-primary-600"
            />
          </div>
        )}
      </div>
    </div>
  )

  if (isPhone) {
    return (
      <div className="fixed inset-0 z-[70]" data-testid="now-playing-root">
        <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
        {panel}
      </div>
    )
  }

  return (
    <div
      className="fixed z-[70] sm:left-[4.5rem] lg:left-[16.5rem]"
      style={{ bottom: abovePlayer('0.5rem') }}
      data-testid="now-playing-root"
    >
      {panel}
    </div>
  )
}
