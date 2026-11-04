// Spec #110 — the viewer's editing contract and the pure helpers behind the
// inline text editor. Kept out of the component files so those export only
// components (react-refresh).
import type { AnnotatedSegment, SegmentKind, TranscriptSpeaker } from '../../api/types'

export const SEGMENT_KINDS: { kind: SegmentKind; label: string }[] = [
  { kind: 'content', label: 'Content' },
  { kind: 'filler', label: 'Filler (hidden)' },
  { kind: 'ad_break', label: 'Ad break' },
  { kind: 'music', label: 'Music' },
  { kind: 'intro', label: 'Intro' },
  { kind: 'outro', label: 'Outro' },
]

export interface SegmentEditing {
  /** Diarization label per effective segment id (for the speaker picker). */
  labelsBySegmentId: Map<number, string>
  speakers: TranscriptSpeaker[]
  /** The segment whose text is open for editing (one at a time). */
  editingSegmentId: number | null
  onEditSegment: (segmentId: number | null) => void
  onSetSpeaker: (segment: AnnotatedSegment, target: { label: string } | { name: string; entityId: string | null }) => void
  /** Opens the name-or-link dialog for one segment ("Someone else…"). */
  onPickSpeakerName: (segment: AnnotatedSegment) => void
  onSetText: (segment: AnnotatedSegment, text: string) => void
  onSetKind: (segment: AnnotatedSegment, kind: SegmentKind) => void
  onSplit: (segment: AnnotatedSegment, textWordIndex: number) => void
  onMergeWithPrevious: (segment: AnnotatedSegment) => void
  /** Writes are refused right now (busy pipeline or a save in flight). */
  disabled: boolean
}

/** The whitespace word the caret sits before, or ``null`` at either end. */
export function wordIndexAtCaret(text: string, caret: number): number | null {
  const before = text.slice(0, caret)
  const after = text.slice(caret)
  if (!before.trim() || !after.trim()) return null
  // A caret mid-word splits the word: the split lands before that word.
  const wordsBefore = before.trim().split(/\s+/).length
  const midWord = !/\s$/.test(before) && !/^\s/.test(after)
  return midWord ? wordsBefore - 1 : wordsBefore
}

const PLACEHOLDER = /^(speaker[_ ]?\d+|unknown( speaker)?|none|null)$/i

/** A label a reader would see as unnamed (``SPEAKER_01``, empty). */
export function isUnnamed(speaker: TranscriptSpeaker): boolean {
  return !speaker.name || PLACEHOLDER.test(speaker.name.trim())
}
