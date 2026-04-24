import {
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type ReactElement,
} from 'react'
import type {
  AnnotatedSegment,
  AnnotatedTranscriptDump,
  SegmentKind,
} from '../api/types'
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
// Global preference, not per-episode: matches reader intent ("I never
// want to see ads" carries across episodes).
const HIDDEN_KINDS_STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

type TogglableKind = Exclude<SegmentKind, 'filler' | 'content'>

const TOGGLE_ORDER: TogglableKind[] = ['ad_break', 'music', 'intro', 'outro']

const KIND_LABELS: Record<TogglableKind, string> = {
  ad_break: 'Ads',
  music: 'Music',
  intro: 'Intro',
  outro: 'Outro',
}

interface BlockStyle {
  border: string
  bg: string
  bgActive: string
  text: string
  accent: string
  hoverRing: string
}

const BLOCK_STYLES: Record<TogglableKind, BlockStyle> = {
  ad_break: {
    border: 'border-amber-400',
    bg: 'bg-amber-50/70',
    bgActive: 'bg-amber-100/80 shadow-sm',
    text: 'text-amber-800',
    accent: 'text-amber-700/80',
    hoverRing:
      'hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400',
  },
  music: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
  intro: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
  outro: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
}

function loadHiddenKinds(): Set<TogglableKind> {
  if (typeof window === 'undefined') return new Set()
  try {
    const raw = window.localStorage.getItem(HIDDEN_KINDS_STORAGE_KEY)
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(
      parsed.filter((value): value is TogglableKind =>
        typeof value === 'string' && (TOGGLE_ORDER as string[]).includes(value),
      ),
    )
  } catch {
    return new Set()
  }
}

function saveHiddenKinds(kinds: Set<TogglableKind>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(
      HIDDEN_KINDS_STORAGE_KEY,
      JSON.stringify(Array.from(kinds)),
    )
  } catch {
    // localStorage may be disabled (private mode, quota exceeded) — the
    // preference silently reverts to the session default.
  }
}

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

type SeekableProps = Pick<
  React.HTMLAttributes<HTMLDivElement>,
  'role' | 'tabIndex' | 'aria-label' | 'onClick' | 'onKeyDown'
>

function seekableProps(
  label: string,
  onSeek: ((seconds: number) => void) | undefined,
  seconds: number,
): SeekableProps {
  if (!onSeek) return {}
  const activate = () => onSeek(seconds)
  return {
    role: 'button',
    tabIndex: 0,
    'aria-label': label,
    onClick: activate,
    onKeyDown: (e) => handleActivationKey(e, activate),
  }
}

function highlightMatches(text: string, query: string) {
  if (!query) return text
  const needle = query.toLowerCase()
  const hay = text.toLowerCase()
  const out: (string | ReactElement)[] = []
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

interface BlockSegmentProps {
  segment: AnnotatedSegment
  kind: TogglableKind
  offset: number
  isActive: boolean
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  // When true, collapse to a compact "[00:02] AD BREAK — NAVAN" chip.
  // When false, render the full cleaned ad body with a subtle coloured rail.
  collapsed: boolean
}

const BlockSegment = memo(function BlockSegment({
  segment,
  kind,
  offset,
  isActive,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  collapsed,
}: BlockSegmentProps) {
  const style = BLOCK_STYLES[kind]
  const baseLabel = kind === 'ad_break' ? 'Ad break' : KIND_LABELS[kind]
  const sponsorSuffix =
    kind === 'ad_break' && segment.sponsor ? ` — ${segment.sponsor}` : ''
  const absoluteSeconds = segment.start + offset
  const dimClass = dimmed ? 'opacity-70' : ''
  const interactive = seekableProps(
    `Seek to ${formatTimestamp(absoluteSeconds)} — ${baseLabel}${sponsorSuffix}`,
    onSeek,
    absoluteSeconds,
  )
  const seekableClasses = onSeek ? `cursor-pointer ${style.hoverRing}` : ''

  if (collapsed) {
    const activeRing = isActive ? style.bgActive : ''
    return (
      <div
        {...interactive}
        ref={registerRef}
        data-testid={`segment-${kind}-${segment.id}`}
        data-active={isActive ? 'true' : 'false'}
        data-collapsed="true"
        className={`my-3 border-l-4 ${style.border} ${style.bg} px-4 py-2 rounded-r-md transition-shadow ${activeRing} ${dimClass} ${seekableClasses}`}
      >
        <div className={`flex items-center gap-3 ${style.text}`}>
          <TimestampLink
            seconds={absoluteSeconds}
            onCopy={onCopyTimestamp}
            className={`font-mono text-[11px] tabular-nums ${style.accent}`}
          />
          <span className="text-xs font-semibold uppercase tracking-wider">
            {baseLabel}
            {highlightMatches(sponsorSuffix, searchQuery)}
          </span>
        </div>
      </div>
    )
  }

  // Expanded: full ad copy reads like body text but keeps the kind's
  // accent colour on the left rail so the reader still clocks it as
  // sponsored content. No label chip — the user reads the ad body directly.
  const containerActive = isActive ? style.bgActive : 'hover:bg-gray-50/70'
  return (
    <div
      {...interactive}
      ref={registerRef}
      data-testid={`segment-${kind}-${segment.id}`}
      data-active={isActive ? 'true' : 'false'}
      data-collapsed="false"
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${dimClass} ${seekableClasses}`}
    >
      <p
        className={`text-gray-800 pl-4 border-l-2 ${style.border} text-base leading-[1.7] !mb-0`}
      >
        <TimestampLink
          seconds={absoluteSeconds}
          onCopy={onCopyTimestamp}
          className={`mr-2 font-mono text-[11px] tabular-nums align-baseline ${style.accent}`}
        />
        {segment.text
          ? highlightMatches(segment.text, searchQuery)
          : (
            <span className="text-gray-400 italic">
              (empty {baseLabel.toLowerCase()})
            </span>
          )}
      </p>
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
}: ContentSegmentProps) {
  const speaker = segment.speaker ?? 'Unknown'
  const absoluteSeconds = segment.start + offset
  const speakerText = getSpeakerColor(speaker)
  const speakerBorder = getSpeakerBorderColor(speaker)
  const containerActive = isActive ? 'bg-primary-50/70' : 'hover:bg-gray-50/70'
  const paragraphBorder = isActive ? speakerBorder : 'border-gray-200'
  const paragraphAccent = 'border-l-2'
  const timestampColor = isActive ? 'text-primary-700' : 'text-gray-400'
  const dimClass = dimmed ? 'opacity-70' : ''
  const bodyClass = isFiller ? 'text-gray-500 italic' : 'text-gray-800'
  const interactive = seekableProps(
    `Seek to ${formatTimestamp(absoluteSeconds)} — ${speaker}`,
    onSeek,
    absoluteSeconds,
  )
  const seekableClasses = onSeek
    ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400'
    : ''
  return (
    <div
      {...interactive}
      ref={registerRef}
      data-testid={`segment-content-${segment.id}`}
      data-active={isActive ? 'true' : 'false'}
      data-filler={isFiller ? 'true' : 'false'}
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${dimClass} ${seekableClasses}`}
    >
      <p
        className={`${bodyClass} pl-4 ${paragraphAccent} ${paragraphBorder} text-base leading-[1.7] !mb-0`}
      >
        <TimestampLink
          seconds={absoluteSeconds}
          onCopy={onCopyTimestamp}
          className={`mr-2 font-mono text-[11px] tabular-nums align-baseline ${timestampColor}`}
        />
        <span className={`font-sans font-semibold tracking-tight ${speakerText}`}>
          {speaker}
          {isFiller && (
            <span className="ml-1.5 font-mono text-[10px] font-normal uppercase tracking-wider text-gray-400">
              filler
            </span>
          )}
          :
        </span>{' '}
        {highlightMatches(segment.text, searchQuery)}
      </p>
    </div>
  )
})

interface ToggleBarProps {
  presentKinds: TogglableKind[]
  hiddenKinds: Set<TogglableKind>
  onToggle: (kind: TogglableKind) => void
}

function ToggleBar({ presentKinds, hiddenKinds, onToggle }: ToggleBarProps) {
  if (presentKinds.length === 0) return null
  return (
    <div
      role="group"
      aria-label="Segment kind filters"
      className="inline-flex flex-wrap items-center gap-1.5"
    >
      <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">Show</span>
      {presentKinds.map((kind) => {
        const isVisible = !hiddenKinds.has(kind)
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={isVisible}
            onClick={() => onToggle(kind)}
            className={
              'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ' +
              (isVisible
                ? 'bg-blue-100 text-blue-800 border border-blue-300'
                : 'bg-gray-100 text-gray-500 border border-gray-200 line-through')
            }
          >
            {KIND_LABELS[kind]}
          </button>
        )
      })}
    </div>
  )
}

// Rows are either a normal segment render or a placeholder for a run of
// non-matching segments collapsed under a single "N hidden" pill.
type RenderRow =
  | { type: 'segment'; segment: AnnotatedSegment; expandedFromHidden: boolean }
  | { type: 'hidden'; firstId: number; count: number }

export default function SegmentedTranscriptViewer({
  transcript,
  episodeId,
  onSeekRequest,
}: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0
  const [followPlayback, setFollowPlayback] = usePersistedBoolean(FOLLOW_STORAGE_KEY, false)
  const [showFiller, setShowFiller] = usePersistedBoolean(SHOW_FILLER_STORAGE_KEY, false)
  const [hiddenKinds, setHiddenKinds] = useState<Set<TogglableKind>>(loadHiddenKinds)
  const [searchInput, setSearchInput] = useState('')
  const searchQuery = useDeferredValue(searchInput.trim())
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0)
  const [expandedHiddenGroups, setExpandedHiddenGroups] = useState<Set<number>>(
    () => new Set(),
  )
  const { showToast } = useToast()

  const toggleKind = useCallback((kind: TogglableKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      saveHiddenKinds(next)
      return next
    })
  }, [])

  // Togglable kinds are always in the rendered list — toggling the pill
  // flips them between compact chip and full-text; it never drops them.
  const { presentKinds, renderedSegments } = useMemo(() => {
    const present = new Set<TogglableKind>()
    const visible: AnnotatedSegment[] = []
    for (const seg of transcript.segments) {
      if (seg.kind === 'filler') {
        if (showFiller) visible.push(seg)
        continue
      }
      if (seg.kind !== 'content') {
        present.add(seg.kind as TogglableKind)
      }
      visible.push(seg)
    }
    return {
      presentKinds: TOGGLE_ORDER.filter((kind) => present.has(kind)),
      renderedSegments: visible,
    }
  }, [transcript.segments, showFiller])

  const currentTime = usePlayerTime()
  const { track } = usePlayer()
  const isCurrentEpisode = !!episodeId && track?.episodeId === episodeId

  const activeSegmentId = useMemo(() => {
    if (!isCurrentEpisode) return null
    // Search the full (unfiltered) list so filler gaps and hidden kinds
    // don't flicker the highlight, then walk back to the nearest visible
    // segment so the outline lands on something the reader can see.
    const idx = findActiveSegmentIndex(transcript.segments, currentTime, offset)
    if (idx < 0) return null
    const visibleIds = new Set(renderedSegments.map((s) => s.id))
    for (let i = idx; i >= 0; i -= 1) {
      if (visibleIds.has(transcript.segments[i].id)) {
        return transcript.segments[i].id
      }
    }
    return null
  }, [isCurrentEpisode, transcript.segments, currentTime, offset, renderedSegments])

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

  const { matchingIds, matchOrder } = useMemo(() => {
    if (!searchQuery) return { matchingIds: null as Set<number> | null, matchOrder: [] as number[] }
    const needle = searchQuery.toLowerCase()
    const ids = new Set<number>()
    const order: number[] = []
    for (const seg of renderedSegments) {
      const hit =
        seg.text.toLowerCase().includes(needle) ||
        (seg.sponsor?.toLowerCase().includes(needle) ?? false) ||
        (seg.speaker?.toLowerCase().includes(needle) ?? false)
      if (hit) {
        ids.add(seg.id)
        order.push(seg.id)
      }
    }
    return { matchingIds: ids, matchOrder: order }
  }, [searchQuery, renderedSegments])

  useEffect(() => {
    setCurrentMatchIndex(0)
    setExpandedHiddenGroups(new Set())
  }, [searchQuery])

  useEffect(() => {
    if (!searchQuery || matchOrder.length === 0) return
    follow.scrollToKey(matchOrder[0])
    // follow is stable from useAutoScrollFollow; intentionally exclude.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, matchOrder])

  const goToMatch = useCallback(
    (delta: number) => {
      if (matchOrder.length === 0) return
      setCurrentMatchIndex((prev) => {
        const next = (prev + delta + matchOrder.length) % matchOrder.length
        follow.scrollToKey(matchOrder[next])
        return next
      })
    },
    [matchOrder, follow],
  )

  const expandHiddenGroup = useCallback((firstId: number) => {
    setExpandedHiddenGroups((prev) => {
      const next = new Set(prev)
      next.add(firstId)
      return next
    })
  }, [])

  const renderRows = useMemo<RenderRow[]>(() => {
    if (!matchingIds) {
      return renderedSegments.map((segment) => ({
        type: 'segment',
        segment,
        expandedFromHidden: false,
      }))
    }
    const rows: RenderRow[] = []
    let hidden: AnnotatedSegment[] = []
    const flushHidden = () => {
      if (hidden.length === 0) return
      const firstId = hidden[0].id
      if (expandedHiddenGroups.has(firstId)) {
        for (const seg of hidden) {
          rows.push({ type: 'segment', segment: seg, expandedFromHidden: true })
        }
      } else {
        rows.push({ type: 'hidden', firstId, count: hidden.length })
      }
      hidden = []
    }
    for (const seg of renderedSegments) {
      if (matchingIds.has(seg.id)) {
        flushHidden()
        rows.push({ type: 'segment', segment: seg, expandedFromHidden: false })
      } else {
        hidden.push(seg)
      }
    }
    flushHidden()
    return rows
  }, [matchingIds, renderedSegments, expandedHiddenGroups])

  if (transcript.segments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript has no segments yet.
        </p>
      </div>
    )
  }

  return (
    <div className="relative">
      <div className="sticky top-0 z-10 -mx-4 sm:-mx-6 px-4 sm:px-6 py-2 mb-3 bg-white border-b border-gray-100 flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="relative flex-1 min-w-[180px]">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search transcript…"
            aria-label="Search transcript"
            className="w-full rounded-md border border-gray-200 bg-white pl-8 pr-3 py-1.5 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
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
        </div>
        {searchQuery && (
          <div className="inline-flex items-center gap-1 text-xs text-gray-600">
            <span className="font-mono tabular-nums min-w-[3.5rem] text-right">
              {matchOrder.length === 0
                ? 'No matches'
                : `${currentMatchIndex + 1} / ${matchOrder.length}`}
            </span>
            <button
              type="button"
              onClick={() => goToMatch(-1)}
              disabled={matchOrder.length === 0}
              aria-label="Previous match"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => goToMatch(1)}
              disabled={matchOrder.length === 0}
              aria-label="Next match"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
        )}
        <ToggleBar
          presentKinds={presentKinds}
          hiddenKinds={hiddenKinds}
          onToggle={toggleKind}
        />
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
      </div>

      {renderedSegments.length === 0 ? (
        <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
          <p className="text-gray-600 font-medium">No content segments visible</p>
          <p className="text-sm text-gray-400 mt-1">
            Every segment in this transcript was marked as filler. Toggle{' '}
            <em>Show filler</em> above to reveal them.
          </p>
        </div>
      ) : (
        <div className="transcript-content leading-relaxed space-y-[3px]">
          {renderRows.map((row) => {
            if (row.type === 'hidden') {
              return (
                <button
                  key={`hidden-${row.firstId}`}
                  type="button"
                  onClick={() => expandHiddenGroup(row.firstId)}
                  className="my-2 w-full inline-flex items-center justify-center gap-2 rounded-md border border-dashed border-gray-200 bg-gray-50/60 py-1.5 px-3 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                  <span>
                    {row.count} segment{row.count === 1 ? '' : 's'} hidden
                  </span>
                  <span className="opacity-60">· Click to show</span>
                </button>
              )
            }
            const { segment, expandedFromHidden } = row
            const isActive = segment.id === activeSegmentId
            if (segment.kind === 'content' || segment.kind === 'filler') {
              return (
                <ContentSegment
                  key={segment.id}
                  segment={segment}
                  offset={offset}
                  isActive={isActive}
                  onSeek={onSeekRequest}
                  registerRef={follow.registerRef(segment.id)}
                  onCopyTimestamp={handleCopyTimestamp}
                  searchQuery={searchQuery}
                  dimmed={expandedFromHidden}
                  isFiller={segment.kind === 'filler'}
                />
              )
            }
            const togglableKind = segment.kind as TogglableKind
            return (
              <BlockSegment
                key={segment.id}
                segment={segment}
                kind={togglableKind}
                offset={offset}
                isActive={isActive}
                onSeek={onSeekRequest}
                registerRef={follow.registerRef(segment.id)}
                onCopyTimestamp={handleCopyTimestamp}
                searchQuery={searchQuery}
                dimmed={expandedFromHidden}
                collapsed={hiddenKinds.has(togglableKind)}
              />
            )
          })}
        </div>
      )}

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
