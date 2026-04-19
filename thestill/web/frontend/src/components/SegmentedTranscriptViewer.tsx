import { memo, useCallback, useMemo, type KeyboardEvent } from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump } from '../api/types'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import {
  useAutoScrollFollow,
  usePersistedBoolean,
} from '../hooks/useAutoScrollFollow'
import { getSpeakerBorderColor, getSpeakerColor } from '../utils/speakerColors'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'

const FOLLOW_STORAGE_KEY = 'thestill:transcript:followPlayback'

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
  // Enables active-segment highlighting when the player is on this episode.
  episodeId?: string | null
  // Called with an absolute playback second when the user clicks or
  // keyboard-activates a segment. Parent decides whether to seek() an
  // already-playing track or play() a new one.
  onSeekRequest?: (seconds: number) => void
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}

function handleActivationKey(event: KeyboardEvent<HTMLDivElement>, onActivate: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    onActivate()
  }
}

interface AdBreakProps {
  segment: AnnotatedSegment
  offset: number
  isActive: boolean
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
}

const AdBreak = memo(function AdBreak({ segment, offset, isActive, onSeek, registerRef }: AdBreakProps) {
  const sponsor = segment.sponsor ? ` — ${segment.sponsor}` : ''
  const activeRing = isActive ? 'ring-2 ring-amber-400/70 shadow-sm' : ''
  const seekable = !!onSeek
  const activate = useCallback(() => onSeek?.(segment.start + offset), [onSeek, segment.start, offset])
  return (
    <div
      ref={registerRef}
      data-active={isActive ? 'true' : 'false'}
      role={seekable ? 'button' : undefined}
      tabIndex={seekable ? 0 : undefined}
      aria-label={seekable ? `Seek to ${formatTimestamp(segment.start + offset)} — ad break${sponsor}` : undefined}
      onClick={seekable ? activate : undefined}
      onKeyDown={seekable ? (e) => handleActivationKey(e, activate) : undefined}
      className={`my-5 border-l-4 border-amber-400 bg-amber-50/70 px-4 py-3 rounded-r-md transition-shadow ${activeRing} ${seekable ? 'cursor-pointer hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400' : ''}`}
    >
      <div className="flex items-center gap-3 text-amber-800">
        <span className="font-mono text-[11px] tabular-nums text-amber-700/80">
          {formatTimestamp(segment.start + offset)}
        </span>
        <span className="text-xs font-semibold uppercase tracking-wider">
          Ad break{sponsor}
        </span>
      </div>
    </div>
  )
})

interface ContentSegmentProps {
  segment: AnnotatedSegment
  offset: number
  isActive: boolean
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
}

const ContentSegment = memo(function ContentSegment({
  segment,
  offset,
  isActive,
  onSeek,
  registerRef,
}: ContentSegmentProps) {
  const speaker = segment.speaker ?? 'Unknown'
  const speakerText = getSpeakerColor(speaker)
  const speakerBorder = getSpeakerBorderColor(speaker)
  const containerActive = isActive
    ? 'bg-primary-50/70 ring-1 ring-primary-100'
    : 'hover:bg-gray-50/70'
  const paragraphBorder = isActive ? speakerBorder : 'border-gray-200'
  const paragraphAccent = isActive ? 'border-l-[3px]' : 'border-l-2'
  const timestampColor = isActive ? 'text-primary-700' : 'text-gray-400'
  const seekable = !!onSeek
  const activate = useCallback(() => onSeek?.(segment.start + offset), [onSeek, segment.start, offset])
  return (
    <div
      ref={registerRef}
      data-active={isActive ? 'true' : 'false'}
      role={seekable ? 'button' : undefined}
      tabIndex={seekable ? 0 : undefined}
      aria-label={
        seekable
          ? `Seek to ${formatTimestamp(segment.start + offset)} — ${speaker}`
          : undefined
      }
      onClick={seekable ? activate : undefined}
      onKeyDown={seekable ? (e) => handleActivationKey(e, activate) : undefined}
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${seekable ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400' : ''}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-1.5">
        <span className={`font-sans text-sm font-semibold tracking-tight ${speakerText}`}>
          {speaker}
        </span>
        <span className={`font-mono text-[11px] tabular-nums ${timestampColor}`}>
          {formatTimestamp(segment.start + offset)}
        </span>
      </div>
      <p
        className={`text-gray-800 pl-4 ${paragraphAccent} ${paragraphBorder} text-[15px] leading-[1.75] sm:text-[17px]`}
      >
        {segment.text}
      </p>
    </div>
  )
})

export default function SegmentedTranscriptViewer({
  transcript,
  episodeId,
  onSeekRequest,
}: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0
  const visibleSegments = useMemo(
    () => transcript.segments.filter((seg) => seg.kind !== 'filler'),
    [transcript.segments],
  )

  // Only listen to the playback tick when this episode is the one the
  // player has loaded. Otherwise we'd rerender the viewer for every
  // timeupdate of an unrelated episode.
  const currentTime = usePlayerTime()
  const { track } = usePlayer()
  const isCurrentEpisode = !!episodeId && track?.episodeId === episodeId

  const [followPlayback, setFollowPlayback] = usePersistedBoolean(FOLLOW_STORAGE_KEY, false)

  const activeSegmentId = useMemo(() => {
    if (!isCurrentEpisode) return null
    // Search over the full (unfiltered) list so filler gaps don't make the
    // highlight flicker, then map back to the nearest visible segment.
    const idx = findActiveSegmentIndex(transcript.segments, currentTime, offset)
    if (idx < 0) return null
    for (let i = idx; i >= 0; i -= 1) {
      if (transcript.segments[i].kind !== 'filler') {
        return transcript.segments[i].id
      }
    }
    return null
  }, [isCurrentEpisode, transcript.segments, currentTime, offset])

  const follow = useAutoScrollFollow({
    activeKey: activeSegmentId,
    enabled: followPlayback && isCurrentEpisode,
  })

  if (visibleSegments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No content segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript's segments have all been marked as filler.
        </p>
      </div>
    )
  }

  return (
    <div className="relative">
      <div className="flex items-center justify-end mb-3">
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={followPlayback}
            onChange={(e) => setFollowPlayback(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Follow playback</span>
        </label>
      </div>

      <div className="transcript-content leading-relaxed space-y-1.5">
        {visibleSegments.map((segment) => {
          const isActive = segment.id === activeSegmentId
          return segment.kind === 'ad_break' ? (
            <AdBreak
              key={segment.id}
              segment={segment}
              offset={offset}
              isActive={isActive}
              onSeek={onSeekRequest}
              registerRef={follow.registerRef(segment.id)}
            />
          ) : (
            <ContentSegment
              key={segment.id}
              segment={segment}
              offset={offset}
              isActive={isActive}
              onSeek={onSeekRequest}
              registerRef={follow.registerRef(segment.id)}
            />
          )
        })}
      </div>

      {followPlayback && follow.userScrolledAway && activeSegmentId != null && (
        <button
          type="button"
          onClick={follow.resume}
          className="fixed bottom-28 right-6 z-20 inline-flex items-center gap-2 rounded-full bg-primary-900 px-4 py-2 text-sm font-medium text-white shadow-lg hover:bg-primary-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
          </svg>
          Resume follow
        </button>
      )}
    </div>
  )
}
