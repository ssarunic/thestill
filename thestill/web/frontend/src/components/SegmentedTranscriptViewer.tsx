import {
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump } from '../api/types'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import {
  useAutoScrollFollow,
  usePersistedBoolean,
} from '../hooks/useAutoScrollFollow'
import { buildTimestampDeepLink, useDeepLinkSeek } from '../hooks/useDeepLinkSeek'
import { getSpeakerBorderColor, getSpeakerColor } from '../utils/speakerColors'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'
import { useToast } from './Toast'

const FOLLOW_STORAGE_KEY = 'thestill:transcript:followPlayback'
const SHOW_FILLER_STORAGE_KEY = 'thestill:transcript:showFiller'

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

// Case-insensitive, non-regex match highlighter. Splits the supplied
// text around each occurrence of `query` and returns a mix of plain
// strings and <mark>-wrapped matches. Returns the original text
// untouched when the query is empty or no match is found.
function highlightMatches(text: string, query: string) {
  if (!query) return text
  const needle = query.toLowerCase()
  const hay = text.toLowerCase()
  const out: (string | JSX.Element)[] = []
  let cursor = 0
  let idx = hay.indexOf(needle, cursor)
  if (idx === -1) return text
  let markKey = 0
  while (idx !== -1) {
    if (idx > cursor) out.push(text.slice(cursor, idx))
    out.push(
      <mark key={markKey++} className="bg-yellow-100 text-gray-900 rounded px-0.5">
        {text.slice(idx, idx + query.length)}
      </mark>,
    )
    cursor = idx + query.length
    idx = hay.indexOf(needle, cursor)
  }
  if (cursor < text.length) out.push(text.slice(cursor))
  return out
}

interface TimestampLinkProps {
  seconds: number
  className: string
  onCopy: (seconds: number) => void
}

function TimestampLink({ seconds, className, onCopy }: TimestampLinkProps) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onCopy(seconds)
      }}
      title="Copy link to this moment"
      className={`${className} hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded`}
    >
      {formatTimestamp(seconds)}
    </button>
  )
}

interface AdBreakProps {
  segment: AnnotatedSegment
  offset: number
  isActive: boolean
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  segmentIndex: number
}

const AdBreak = memo(function AdBreak({
  segment,
  offset,
  isActive,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  segmentIndex,
}: AdBreakProps) {
  const sponsor = segment.sponsor ? ` — ${segment.sponsor}` : ''
  const activeRing = isActive ? 'ring-2 ring-amber-400/70 shadow-sm' : ''
  const seekable = !!onSeek
  const activate = useCallback(() => onSeek?.(segment.start + offset), [onSeek, segment.start, offset])
  const dimClass = dimmed ? 'opacity-40' : ''
  return (
    <div
      ref={registerRef}
      data-active={isActive ? 'true' : 'false'}
      data-segment-index={segmentIndex}
      role={seekable ? 'button' : undefined}
      tabIndex={seekable ? 0 : undefined}
      aria-label={seekable ? `Seek to ${formatTimestamp(segment.start + offset)} — ad break${sponsor}` : undefined}
      onClick={seekable ? activate : undefined}
      onKeyDown={seekable ? (e) => handleActivationKey(e, activate) : undefined}
      className={`my-5 border-l-4 border-amber-400 bg-amber-50/70 px-4 py-3 rounded-r-md transition-shadow ${activeRing} ${dimClass} ${seekable ? 'cursor-pointer hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400' : ''}`}
    >
      <div className="flex items-center gap-3 text-amber-800">
        <TimestampLink
          seconds={segment.start + offset}
          onCopy={onCopyTimestamp}
          className="font-mono text-[11px] tabular-nums text-amber-700/80"
        />
        <span className="text-xs font-semibold uppercase tracking-wider">
          Ad break{highlightMatches(sponsor, searchQuery)}
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
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  isFiller: boolean
  segmentIndex: number
}

const ContentSegment = memo(function ContentSegment({
  segment,
  offset,
  isActive,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  isFiller,
  segmentIndex,
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
  const dimClass = dimmed ? 'opacity-40' : ''
  const bodyClass = isFiller ? 'text-gray-500 italic' : 'text-gray-800'
  return (
    <div
      ref={registerRef}
      data-active={isActive ? 'true' : 'false'}
      data-filler={isFiller ? 'true' : 'false'}
      data-segment-index={segmentIndex}
      role={seekable ? 'button' : undefined}
      tabIndex={seekable ? 0 : undefined}
      aria-label={
        seekable
          ? `Seek to ${formatTimestamp(segment.start + offset)} — ${speaker}`
          : undefined
      }
      onClick={seekable ? activate : undefined}
      onKeyDown={seekable ? (e) => handleActivationKey(e, activate) : undefined}
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${dimClass} ${seekable ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400' : ''}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-1.5">
        <span className={`font-sans text-sm font-semibold tracking-tight ${speakerText}`}>
          {speaker}
          {isFiller && (
            <span className="ml-1.5 font-mono text-[10px] font-normal uppercase tracking-wider text-gray-400">
              filler
            </span>
          )}
        </span>
        <TimestampLink
          seconds={segment.start + offset}
          onCopy={onCopyTimestamp}
          className={`font-mono text-[11px] tabular-nums ${timestampColor}`}
        />
      </div>
      <p
        className={`${bodyClass} pl-4 ${paragraphAccent} ${paragraphBorder} text-[15px] leading-[1.75] sm:text-[17px]`}
      >
        {highlightMatches(segment.text, searchQuery)}
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
  const [followPlayback, setFollowPlayback] = usePersistedBoolean(FOLLOW_STORAGE_KEY, false)
  const [showFiller, setShowFiller] = usePersistedBoolean(SHOW_FILLER_STORAGE_KEY, false)
  const [searchInput, setSearchInput] = useState('')
  // React's useDeferredValue gives us a cheap debounce: typing stays
  // responsive while the re-renders lag one frame behind.
  const searchQuery = useDeferredValue(searchInput.trim())
  const [showHelp, setShowHelp] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const { showToast } = useToast()

  const renderedSegments = useMemo(
    () =>
      showFiller
        ? transcript.segments
        : transcript.segments.filter((seg) => seg.kind !== 'filler'),
    [showFiller, transcript.segments],
  )

  // Only listen to the playback tick when this episode is the one the
  // player has loaded. Otherwise we'd rerender the viewer for every
  // timeupdate of an unrelated episode.
  const currentTime = usePlayerTime()
  const { track } = usePlayer()
  const isCurrentEpisode = !!episodeId && track?.episodeId === episodeId

  const activeSegmentId = useMemo(() => {
    if (!isCurrentEpisode) return null
    // Search over the full (unfiltered) list so filler gaps don't make the
    // highlight flicker, then map back to the nearest non-filler segment
    // (unless filler is visible, in which case the raw match is fine).
    const idx = findActiveSegmentIndex(transcript.segments, currentTime, offset)
    if (idx < 0) return null
    if (showFiller) return transcript.segments[idx].id
    for (let i = idx; i >= 0; i -= 1) {
      if (transcript.segments[i].kind !== 'filler') {
        return transcript.segments[i].id
      }
    }
    return null
  }, [isCurrentEpisode, transcript.segments, currentTime, offset, showFiller])

  const follow = useAutoScrollFollow({
    activeKey: activeSegmentId,
    enabled: followPlayback && isCurrentEpisode,
  })

  const handleCopyTimestamp = useCallback(
    async (seconds: number) => {
      const url = buildTimestampDeepLink(seconds)
      try {
        await navigator.clipboard.writeText(url)
        showToast(`Copied link at ${formatTimestamp(seconds)}`, 'success')
      } catch {
        showToast('Failed to copy link', 'error')
      }
    },
    [showToast],
  )

  useDeepLinkSeek(
    episodeId ?? null,
    useCallback(
      (seconds: number) => onSeekRequest?.(seconds),
      [onSeekRequest],
    ),
    !!onSeekRequest && transcript.segments.length > 0,
  )

  // Pre-compute matching ids so dimming is O(1) per segment render.
  const matchingIds = useMemo(() => {
    if (!searchQuery) return null
    const needle = searchQuery.toLowerCase()
    const set = new Set<number>()
    for (const seg of renderedSegments) {
      if (seg.text.toLowerCase().includes(needle)) set.add(seg.id)
      else if (seg.sponsor && seg.sponsor.toLowerCase().includes(needle)) set.add(seg.id)
      else if (seg.speaker && seg.speaker.toLowerCase().includes(needle)) set.add(seg.id)
    }
    return set
  }, [searchQuery, renderedSegments])

  const matchCount = matchingIds?.size ?? 0

  // Keyboard shortcuts. Scoped to the window, but bailouts keep typing
  // in any input/textarea/contenteditable safe. j/k and ↓/↑ move focus
  // between segments; f toggles follow; / focuses the search input;
  // Shift+? opens the keybindings sheet; Escape closes it.
  useEffect(() => {
    const isTypingTarget = (el: EventTarget | null) => {
      if (!(el instanceof HTMLElement)) return false
      if (el.isContentEditable) return true
      const tag = el.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    }
    const focusSegmentAt = (nextIndex: number) => {
      const list = listRef.current
      if (!list) return
      const max = renderedSegments.length - 1
      const clamped = Math.min(max, Math.max(0, nextIndex))
      const node = list.querySelector<HTMLElement>(`[data-segment-index="${clamped}"]`)
      if (node) {
        node.focus({ preventScroll: true })
        const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        node.scrollIntoView?.({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
      }
    }
    const currentFocusIndex = () => {
      const el = document.activeElement
      if (!(el instanceof HTMLElement)) return -1
      const idx = el.getAttribute('data-segment-index')
      return idx == null ? -1 : Number(idx)
    }
    const handler = (e: KeyboardEvent | globalThis.KeyboardEvent) => {
      // '/' focuses search even from outside the list, as long as we're
      // not already typing somewhere.
      if (e.key === '/' && !isTypingTarget(e.target)) {
        e.preventDefault()
        searchInputRef.current?.focus()
        searchInputRef.current?.select()
        return
      }
      if (e.key === '?' && !isTypingTarget(e.target)) {
        e.preventDefault()
        setShowHelp((prev) => !prev)
        return
      }
      if (e.key === 'Escape') {
        if (showHelp) {
          setShowHelp(false)
          return
        }
        if (document.activeElement === searchInputRef.current && searchInput) {
          setSearchInput('')
          return
        }
      }
      if (isTypingTarget(e.target)) return
      if (e.key === 'j' || e.key === 'ArrowDown') {
        const from = currentFocusIndex()
        if (from === -1) {
          focusSegmentAt(0)
        } else {
          focusSegmentAt(from + 1)
        }
        e.preventDefault()
        return
      }
      if (e.key === 'k' || e.key === 'ArrowUp') {
        const from = currentFocusIndex()
        if (from === -1) {
          focusSegmentAt(0)
        } else {
          focusSegmentAt(from - 1)
        }
        e.preventDefault()
        return
      }
      if (e.key === 'f') {
        setFollowPlayback(!followPlayback)
        e.preventDefault()
        return
      }
    }
    window.addEventListener('keydown', handler as (e: globalThis.KeyboardEvent) => void)
    return () => window.removeEventListener('keydown', handler as (e: globalThis.KeyboardEvent) => void)
  }, [renderedSegments.length, followPlayback, setFollowPlayback, showHelp, searchInput])

  if (transcript.segments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No content segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript has no segments yet.
        </p>
      </div>
    )
  }

  if (renderedSegments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No content segments visible</p>
        <p className="text-sm text-gray-400 mt-1">
          Every segment in this transcript was marked as filler. Toggle{' '}
          <em>Show filler</em> above to reveal them.
        </p>
      </div>
    )
  }

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <div className="relative flex-1 min-w-[200px]">
          <input
            ref={searchInputRef}
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search transcript (press /)"
            aria-label="Search transcript"
            className="w-full rounded-md border border-gray-200 bg-white pl-8 pr-10 py-1.5 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          />
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z" />
          </svg>
          {searchQuery && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] font-mono tabular-nums text-gray-400">
              {matchCount}
            </span>
          )}
        </div>
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={showFiller}
            onChange={(e) => setShowFiller(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Show filler</span>
        </label>
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={followPlayback}
            onChange={(e) => setFollowPlayback(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Follow playback</span>
        </label>
        <button
          type="button"
          onClick={() => setShowHelp(true)}
          className="text-xs text-gray-500 hover:text-gray-700 underline underline-offset-2 decoration-dotted"
          aria-label="Show keyboard shortcuts"
          title="Keyboard shortcuts (?)"
        >
          ?
        </button>
      </div>

      <div ref={listRef} className="transcript-content leading-relaxed space-y-1.5">
        {renderedSegments.map((segment, index) => {
          const isActive = segment.id === activeSegmentId
          const dimmed = !!matchingIds && !matchingIds.has(segment.id)
          return segment.kind === 'ad_break' ? (
            <AdBreak
              key={segment.id}
              segment={segment}
              offset={offset}
              isActive={isActive}
              onSeek={onSeekRequest}
              registerRef={follow.registerRef(segment.id)}
              onCopyTimestamp={handleCopyTimestamp}
              searchQuery={searchQuery}
              dimmed={dimmed}
              segmentIndex={index}
            />
          ) : (
            <ContentSegment
              key={segment.id}
              segment={segment}
              offset={offset}
              isActive={isActive}
              onSeek={onSeekRequest}
              registerRef={follow.registerRef(segment.id)}
              onCopyTimestamp={handleCopyTimestamp}
              searchQuery={searchQuery}
              dimmed={dimmed}
              isFiller={segment.kind === 'filler'}
              segmentIndex={index}
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

      {showHelp && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Transcript keyboard shortcuts"
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 px-4"
          onClick={() => setShowHelp(false)}
        >
          <div
            className="bg-white rounded-lg shadow-xl p-5 w-full max-w-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-900">Keyboard shortcuts</h3>
              <button
                type="button"
                onClick={() => setShowHelp(false)}
                className="text-gray-400 hover:text-gray-600"
                aria-label="Close"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <dl className="space-y-2 text-sm">
              {[
                ['j  or  ↓', 'Next segment'],
                ['k  or  ↑', 'Previous segment'],
                ['Enter / Space', 'Seek to focused segment'],
                ['f', 'Toggle follow playback'],
                ['/', 'Focus search'],
                ['Esc', 'Clear search / close help'],
                ['?', 'Show this help'],
              ].map(([keys, label]) => (
                <div key={keys} className="flex items-baseline justify-between gap-3">
                  <dt className="font-mono text-xs text-gray-500">{keys}</dt>
                  <dd className="text-gray-800">{label}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </div>
  )
}
