import Button from '../Button'
import type { AnnotatedSegment, SegmentKind, TranscriptSpeaker } from '../../api/types'
import { SEGMENT_KINDS } from './segmentEditing'
import { formatClock } from './time'

// Spec #110 §5.3 — the selected row's properties, where the first build
// put inline controls into the sentence: speaker, kind, start and end
// (read-only until Phase 4), Edit text, and Hide (kind "filler", the
// non-destructive delete).

interface SegmentInspectorProps {
  segment: AnnotatedSegment | null
  label: string | null
  speakers: TranscriptSpeaker[]
  offset: number
  disabled: boolean
  onSetSpeaker: (segment: AnnotatedSegment, target: { label: string }) => void
  onPickSpeakerName: (segment: AnnotatedSegment) => void
  onSetKind: (segment: AnnotatedSegment, kind: SegmentKind) => void
  onEditText: (segment: AnnotatedSegment) => void
}

const SOMEONE_ELSE = '__someone_else__'

export default function SegmentInspector({
  segment,
  label,
  speakers,
  offset,
  disabled,
  onSetSpeaker,
  onPickSpeakerName,
  onSetKind,
  onEditText,
}: SegmentInspectorProps) {
  if (!segment) {
    return (
      <section aria-label="Segment" className="px-3 py-3 text-xs text-muted">
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Segment</h3>
        Select a row, a block on the timeline, or press ↓.
      </section>
    )
  }
  const selectClass =
    'w-full rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-60'
  return (
    <section aria-label="Segment" className="space-y-2 px-3 py-3" data-testid="segment-inspector">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Segment</h3>
      <label className="block text-xs text-muted">
        Speaker
        <select
          className={`${selectClass} mt-0.5`}
          value={label ?? SOMEONE_ELSE}
          disabled={disabled}
          aria-label="Speaker"
          onChange={(e) => {
            if (e.target.value === SOMEONE_ELSE) onPickSpeakerName(segment)
            else if (e.target.value !== label) onSetSpeaker(segment, { label: e.target.value })
          }}
        >
          {speakers.map((speaker) => (
            <option key={speaker.label} value={speaker.label}>
              {speaker.name ?? speaker.label} ({speaker.label})
            </option>
          ))}
          <option value={SOMEONE_ELSE}>{label ? 'Someone else…' : `${segment.speaker ?? 'Unknown'} (named)`}</option>
        </select>
      </label>
      {label === null && (
        // The select already sits on the named option, so picking it
        // again fires nothing: a button reopens the name dialog.
        <Button variant="ghost" size="sm" disabled={disabled} onClick={() => onPickSpeakerName(segment)}>
          Change name or link…
        </Button>
      )}
      <label className="block text-xs text-muted">
        Kind
        <select
          className={`${selectClass} mt-0.5`}
          value={segment.kind}
          disabled={disabled}
          aria-label="Kind"
          onChange={(e) => {
            const kind = e.target.value as SegmentKind
            if (kind !== segment.kind) onSetKind(segment, kind)
          }}
        >
          {SEGMENT_KINDS.map(({ kind, label: kindLabel }) => (
            <option key={kind} value={kind}>
              {kindLabel}
            </option>
          ))}
        </select>
      </label>
      <p className="font-mono text-xs tabular-nums text-gray-700">
        {formatClock(segment.start + offset, true)} – {formatClock(segment.end + offset, true)}
        <span className="ml-1 text-gray-400">({(segment.end - segment.start).toFixed(1)} s)</span>
      </p>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" onClick={() => onEditText(segment)} disabled={disabled}>
          Edit text
        </Button>
        {segment.kind !== 'filler' && (
          <Button variant="danger" size="sm" onClick={() => onSetKind(segment, 'filler')} disabled={disabled} title="Marks the segment as filler, which readers never see">
            Hide
          </Button>
        )}
      </div>
    </section>
  )
}
