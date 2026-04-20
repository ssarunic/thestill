import { useMemo, useState } from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump, SegmentKind } from '../api/types'
import { getSpeakerColor } from '../utils/speakerColors'

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
}

type TogglableKind = Exclude<SegmentKind, 'filler' | 'content'>

const TOGGLE_ORDER: TogglableKind[] = ['ad_break', 'music', 'intro', 'outro']

const KIND_LABELS: Record<TogglableKind, string> = {
  ad_break: 'Ads',
  music: 'Music',
  intro: 'Intro',
  outro: 'Outro',
}

// One global preference, not per-episode: matches how readers use the
// site ("I never want to see ads").
const HIDDEN_KINDS_STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

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

const BLOCK_STYLES: Record<TogglableKind, { border: string; bg: string; text: string }> = {
  ad_break: { border: 'border-amber-400', bg: 'bg-amber-50', text: 'text-amber-800' },
  music: { border: 'border-slate-300', bg: 'bg-slate-50', text: 'text-slate-700' },
  intro: { border: 'border-slate-300', bg: 'bg-slate-50', text: 'text-slate-700' },
  outro: { border: 'border-slate-300', bg: 'bg-slate-50', text: 'text-slate-700' },
}

function BlockSegment({
  segment,
  offset,
  kind,
}: {
  segment: AnnotatedSegment
  offset: number
  kind: TogglableKind
}) {
  const { border, bg, text } = BLOCK_STYLES[kind]
  const label = kind === 'ad_break' ? `Ad break${segment.sponsor ? ` — ${segment.sponsor}` : ''}` : KIND_LABELS[kind]
  return (
    <div
      data-testid={`segment-${kind}-${segment.id}`}
      className={`my-4 border-l-4 ${border} ${bg} px-4 py-3 rounded-r`}
    >
      <div className={`flex items-center gap-2 text-sm font-medium ${text}`}>
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
  if (segment.kind === 'filler') return null
  if (segment.kind === 'content') {
    return <ContentSegment key={segment.id} segment={segment} offset={offset} />
  }
  return <BlockSegment key={segment.id} segment={segment} offset={offset} kind={segment.kind} />
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

  const [hiddenKinds, setHiddenKinds] = useState<Set<TogglableKind>>(loadHiddenKinds)

  const { presentKinds, visibleSegments } = useMemo(() => {
    const present = new Set<TogglableKind>()
    const visible: AnnotatedSegment[] = []
    for (const seg of transcript.segments) {
      if (seg.kind === 'filler') continue
      if (seg.kind !== 'content') {
        present.add(seg.kind as TogglableKind)
        if (hiddenKinds.has(seg.kind as TogglableKind)) continue
      }
      visible.push(seg)
    }
    return {
      presentKinds: TOGGLE_ORDER.filter((kind) => present.has(kind)),
      visibleSegments: visible,
    }
  }, [transcript.segments, hiddenKinds])

  const toggleKind = (kind: TogglableKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      saveHiddenKinds(next)
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
