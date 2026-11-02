import { Fragment, memo } from 'react'
import type { AnnotatedSegment, KaraokeWordsByEpisode, WordTimestamp } from '../../api/types'
import { EditableText, SpeakerPicker } from './EditableSegment'
import type { SegmentEditing } from './segmentEditing'
import { formatClock } from './time'

// Spec #110 §5.4 — the transcript as turns, full width: speaker name and
// start time in a gutter, the end time beneath, the text to the right. The
// selected and active rows show their raw words so a click plays from that
// word; double-click (or Enter) opens the text for editing; a merge handle
// sits on the rail between adjacent rows of the same kind.

const KIND_TAG: Record<string, string> = {
  filler: 'filler',
  ad_break: 'ad',
  music: 'music',
  intro: 'intro',
  outro: 'outro',
}

export interface EditorTranscriptProps {
  segments: AnnotatedSegment[]
  offset: number
  colors: Map<string, string>
  labelsBySegmentId: Map<number, string>
  editing: SegmentEditing
  selectedId: number | null
  activeId: number | null
  focusLabel: string | null
  words: KaraokeWordsByEpisode | null
  conflictSegmentIds: Set<number>
  onSelect: (segmentId: number) => void
  onSeek: (seconds: number) => void
  onMerge: (first: AnnotatedSegment, second: AnnotatedSegment) => void
  registerRef: (key: number) => (el: HTMLElement | null) => void
}

function MergeHandle({ visible, onClick }: { visible: boolean; onClick: () => void }) {
  return (
    <div className="relative h-0">
      <button
        type="button"
        aria-label="Merge segments"
        title="Merge segments (M on the lower row)"
        onClick={onClick}
        className={`absolute left-[9.25rem] top-1/2 z-10 -translate-y-1/2 rounded-full border border-gray-300 bg-white p-0.5 text-gray-500 shadow-sm transition-opacity hover:bg-gray-50 hover:text-primary-700 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
          visible ? 'opacity-100' : 'opacity-0 group-hover/rows:opacity-100'
        }`}
      >
        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 4v5m0 6v5m10-16v5m0 6v5M7 9c0 3 10 3 10 6M7 15c0-3 10-3 10-6" />
        </svg>
      </button>
    </div>
  )
}

interface RowProps {
  segment: AnnotatedSegment
  offset: number
  color: string
  editing: SegmentEditing
  selected: boolean
  active: boolean
  dimmed: boolean
  conflict: boolean
  words: WordTimestamp[] | null
  wordsOffset: number
  onSelect: (segmentId: number) => void
  onSeek: (seconds: number) => void
  registerRef: (el: HTMLElement | null) => void
}

const Row = memo(function Row({
  segment,
  offset,
  color,
  editing,
  selected,
  active,
  dimmed,
  conflict,
  words,
  wordsOffset,
  onSelect,
  onSeek,
  registerRef,
}: RowProps) {
  const start = segment.start + offset
  const showWords = Boolean(words && words.length > 0 && !segment.metadata?.words_stale && (selected || active))
  const tag = KIND_TAG[segment.kind]
  const body = showWords ? (
    words!.map((w, i) => (
      <Fragment key={i}>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onSeek(w.s + wordsOffset)
          }}
          className="rounded px-px hover:bg-primary-50 hover:text-primary-900 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary-400"
          title={`Play from ${formatClock(w.s + wordsOffset, true)}`}
        >
          {w.w}
        </button>
        {i < words!.length - 1 ? ' ' : ''}
      </Fragment>
    ))
  ) : (
    segment.text || <span className="italic text-gray-400">(empty)</span>
  )
  return (
    <div
      ref={registerRef}
      data-testid={`editor-row-${segment.id}`}
      data-selected={selected ? 'true' : undefined}
      data-active={active ? 'true' : undefined}
      onClick={() => onSelect(segment.id)}
      onDoubleClick={() => {
        if (!editing.disabled) editing.onEditSegment(segment.id)
      }}
      className={`grid grid-cols-[9rem_minmax(0,1fr)] gap-x-4 rounded-lg px-2 py-2 transition-colors ${
        selected ? 'bg-primary-50/70' : active ? 'bg-gray-50' : 'hover:bg-gray-50/70'
      } ${dimmed ? 'opacity-40' : ''}`}
    >
      <div className="min-w-0 border-l-2 pl-3" style={{ borderLeftColor: selected || active ? color : 'transparent' }}>
        <SpeakerPicker segment={segment} editing={editing} color={color} />
        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] tabular-nums text-gray-500">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onSeek(start)
            }}
            title="Play from here"
            className="rounded hover:text-primary-700 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary-400"
          >
            {formatClock(start, true)}
          </button>
          <span className="text-gray-300">–</span>
          <span>{formatClock(segment.end + offset, true)}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-1 font-mono text-[10px] uppercase tracking-wider">
          {tag && <span className="rounded bg-amber-50 px-1 text-amber-800">{tag}</span>}
          {segment.metadata?.edited ? <span className="rounded bg-primary-50 px-1 text-primary-700">edited</span> : null}
          {conflict && <span className="rounded bg-amber-100 px-1 text-amber-900">conflict</span>}
        </div>
      </div>
      <div className={`min-w-0 text-base leading-[1.7] ${segment.kind === 'filler' ? 'italic text-gray-500' : 'text-gray-800'}`}>
        <EditableText segment={segment} editing={editing}>
          {body}
        </EditableText>
      </div>
    </div>
  )
})

export default function EditorTranscript({
  segments,
  offset,
  colors,
  labelsBySegmentId,
  editing,
  selectedId,
  activeId,
  focusLabel,
  words,
  conflictSegmentIds,
  onSelect,
  onSeek,
  onMerge,
  registerRef,
}: EditorTranscriptProps) {
  if (segments.length === 0) {
    return <p className="py-16 text-center text-sm text-muted">This transcript has no segments.</p>
  }
  return (
    <div className="space-y-px" role="list" aria-label="Transcript segments">
      {segments.map((segment, index) => {
        const label = labelsBySegmentId.get(segment.id) ?? null
        const previous = index > 0 ? segments[index - 1] : null
        const canMerge = previous !== null && previous.kind === segment.kind
        const handleVisible = selectedId === segment.id || (previous !== null && selectedId === previous.id)
        return (
          <div key={segment.id} role="listitem" className="group/rows">
            {canMerge && !editing.disabled && (
              <MergeHandle visible={handleVisible} onClick={() => onMerge(previous!, segment)} />
            )}
            <Row
              segment={segment}
              offset={offset}
              color={colors.get(label ?? '') ?? '#4b5563'}
              editing={editing}
              selected={selectedId === segment.id}
              active={activeId === segment.id}
              dimmed={focusLabel !== null && label !== focusLabel}
              conflict={conflictSegmentIds.has(segment.id)}
              words={words?.wordsBySegmentId.get(segment.id) ?? null}
              wordsOffset={words?.offset ?? offset}
              onSelect={onSelect}
              onSeek={onSeek}
              registerRef={registerRef(segment.id)}
            />
          </div>
        )
      })}
    </div>
  )
}
