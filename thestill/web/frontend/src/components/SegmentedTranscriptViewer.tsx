import { useEffect, useMemo, useState } from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump, SegmentKind } from '../api/types'
import { getSpeakerColor } from '../utils/speakerColors'

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
}

// Kinds the reader can toggle. `filler` carries no text and exists in
// the JSON only as a source anchor — it is never rendered. `content`
// is always shown (hiding it would leave an empty page). Every other
// kind gets a toggle pill.
type TogglableKind = Exclude<SegmentKind, 'filler' | 'content'>

const TOGGLE_ORDER: TogglableKind[] = ['ad_break', 'music', 'intro', 'outro']

const KIND_LABELS: Record<TogglableKind, string> = {
  ad_break: 'Ads',
  music: 'Music',
  intro: 'Intro',
  outro: 'Outro',
}

// Persist reader preference across episodes so the choice survives
// navigation. One global key — not per-episode — matches how readers
// use the site ("I never want to see ads").
const HIDDEN_KINDS_STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

function loadHiddenKinds(): Set<TogglableKind> {
  if (typeof window === 'undefined') return new Set()
  try {
    const raw = window.localStorage.getItem(HIDDEN_KINDS_STORAGE_KEY)
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    const valid = parsed.filter((value): value is TogglableKind =>
      typeof value === 'string' && (TOGGLE_ORDER as string[]).includes(value),
    )
    return new Set(valid)
  } catch {
    return new Set()
  }
}

function saveHiddenKinds(kinds: Set<TogglableKind>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(HIDDEN_KINDS_STORAGE_KEY, JSON.stringify(Array.from(kinds)))
  } catch {
    // localStorage may be disabled (private mode, quota) — preference
    // reverts to the session default rather than breaking the viewer.
  }
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}

function AdBreakSegment({ segment, offset }: { segment: AnnotatedSegment; offset: number }) {
  const suffix = segment.sponsor ? ` — ${segment.sponsor}` : ''
  return (
    <div
      data-testid={`segment-ad_break-${segment.id}`}
      className="my-4 border-l-4 border-amber-400 bg-amber-50 px-4 py-3 rounded-r"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
        <span className="font-mono text-xs">[{formatTimestamp(segment.start + offset)}]</span>
        <span>Ad break{suffix}</span>
      </div>
      {segment.text ? (
        <p className="text-gray-800 mt-2 text-base leading-[1.7] whitespace-pre-wrap">{segment.text}</p>
      ) : null}
    </div>
  )
}

function TaggedSegment({
  segment,
  offset,
  label,
}: {
  segment: AnnotatedSegment
  offset: number
  label: string
}) {
  return (
    <div
      data-testid={`segment-${segment.kind}-${segment.id}`}
      className="my-4 border-l-4 border-slate-300 bg-slate-50 px-4 py-3 rounded-r"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <span className="font-mono text-xs">[{formatTimestamp(segment.start + offset)}]</span>
        <span>{label}</span>
      </div>
      {segment.text ? (
        <p className="text-gray-800 mt-2 text-base leading-[1.7] whitespace-pre-wrap">{segment.text}</p>
      ) : null}
    </div>
  )
}

function ContentSegment({ segment, offset }: { segment: AnnotatedSegment; offset: number }) {
  const speaker = segment.speaker ?? 'Unknown'
  return (
    <div data-testid={`segment-content-${segment.id}`} className="mb-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="font-mono text-xs text-gray-400">
          [{formatTimestamp(segment.start + offset)}]
        </span>
        <span className={`font-sans font-semibold ${getSpeakerColor(speaker)}`}>{speaker}:</span>
      </div>
      <p className="text-gray-800 pl-4 border-l-2 border-gray-200 text-base leading-[1.7] sm:text-lg">
        {segment.text}
      </p>
    </div>
  )
}

function renderSegment(segment: AnnotatedSegment, offset: number) {
  switch (segment.kind) {
    case 'ad_break':
      return <AdBreakSegment key={segment.id} segment={segment} offset={offset} />
    case 'music':
      return <TaggedSegment key={segment.id} segment={segment} offset={offset} label="Music" />
    case 'intro':
      return <TaggedSegment key={segment.id} segment={segment} offset={offset} label="Intro" />
    case 'outro':
      return <TaggedSegment key={segment.id} segment={segment} offset={offset} label="Outro" />
    case 'filler':
      // Defensive fallthrough — filler should already be filtered out
      // before this function runs. Returning null keeps React happy
      // without disturbing sibling keys.
      return null
    case 'content':
    default:
      return <ContentSegment key={segment.id} segment={segment} offset={offset} />
  }
}

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
      className="flex flex-wrap items-center gap-2 mb-4 pb-3 border-b border-gray-200"
    >
      <span className="text-xs uppercase tracking-wide text-gray-500">Show</span>
      {presentKinds.map((kind) => {
        const isVisible = !hiddenKinds.has(kind)
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={isVisible}
            onClick={() => onToggle(kind)}
            className={
              'inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-colors ' +
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

export default function SegmentedTranscriptViewer({ transcript }: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0

  const [hiddenKinds, setHiddenKinds] = useState<Set<TogglableKind>>(() => loadHiddenKinds())

  useEffect(() => {
    saveHiddenKinds(hiddenKinds)
  }, [hiddenKinds])

  const presentKinds = useMemo(() => {
    const present = new Set<TogglableKind>()
    for (const seg of transcript.segments) {
      if ((TOGGLE_ORDER as string[]).includes(seg.kind)) {
        present.add(seg.kind as TogglableKind)
      }
    }
    return TOGGLE_ORDER.filter((kind) => present.has(kind))
  }, [transcript.segments])

  const visibleSegments = useMemo(
    () =>
      transcript.segments.filter((seg) => {
        if (seg.kind === 'filler') return false
        if (seg.kind === 'content') return true
        return !hiddenKinds.has(seg.kind as TogglableKind)
      }),
    [transcript.segments, hiddenKinds],
  )

  const toggleKind = (kind: TogglableKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) {
        next.delete(kind)
      } else {
        next.add(kind)
      }
      return next
    })
  }

  if (transcript.segments.length === 0) {
    return (
      <div className="text-center py-12 min-h-[300px]">
        <p className="text-gray-600 font-medium">No segments available</p>
      </div>
    )
  }

  return (
    <div className="transcript-content leading-relaxed">
      <ToggleBar presentKinds={presentKinds} hiddenKinds={hiddenKinds} onToggle={toggleKind} />
      {visibleSegments.length === 0 ? (
        <div className="text-center py-8 text-gray-500 text-sm">
          All segment kinds are hidden. Toggle one on above to see content.
        </div>
      ) : (
        <div className="space-y-1">{visibleSegments.map((seg) => renderSegment(seg, offset))}</div>
      )}
    </div>
  )
}
