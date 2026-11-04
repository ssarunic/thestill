import Button from '../Button'
import type { TranscriptEditConflict, TranscriptEdit } from '../../api/types'
import { describeOp } from './describeOp'

// Spec #110 §3 — an op a re-clean or re-transcribe broke is listed, never
// dropped. The admin reads what it was against and either re-applies it by
// hand (a fresh op) or drops it (a revert row).

const REASONS: Record<string, string> = {
  retranscribed: 'The episode was re-transcribed; the speaker labels and segments it named no longer exist.',
  text_changed: 'A re-clean rewrote this segment.',
  anchor_missing: 'A re-clean no longer produces this segment.',
  anchor_ambiguous: 'A re-clean produced more than one segment with these anchors.',
  label_missing: 'This speaker label is not in the transcript any more.',
  not_adjacent: 'The two segments are no longer next to each other.',
  raw_missing: 'The raw transcript is missing, so the edit cannot be checked.',
  invalid: 'The edit could not be read.',
}

interface ConflictsListProps {
  conflicts: TranscriptEditConflict[]
  edits: TranscriptEdit[]
  onDrop: (editId: string) => void
  className?: string
}

export default function ConflictsList({ conflicts, edits, onDrop, className = '' }: ConflictsListProps) {
  const byId = new Map(edits.map((e) => [e.id, e]))
  return (
    <ul className={`space-y-2 ${className}`} aria-label="Edits that no longer apply">
      {conflicts.map((conflict) => {
        const edit = byId.get(conflict.edit_id)
        const oldText = edit?.op === 'segment.set_text' ? String(edit.args.new_text ?? '') : null
        const currentText = typeof conflict.detail.current_text === 'string' ? conflict.detail.current_text : null
        return (
          <li
            key={conflict.edit_id}
            className="rounded-md border border-amber-200 bg-surface px-3 py-2 text-sm text-ink"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">
                  #{conflict.seq} {edit ? describeOp(edit) : conflict.op}
                </p>
                <p className="text-xs text-muted">{REASONS[conflict.reason] ?? conflict.reason}</p>
                {oldText && (
                  <p className="mt-1 text-xs">
                    <span className="text-muted">Your text: </span>
                    {oldText}
                  </p>
                )}
                {currentText && (
                  <p className="mt-1 text-xs">
                    <span className="text-muted">Now reads: </span>
                    {currentText}
                  </p>
                )}
              </div>
              <Button variant="secondary" size="sm" onClick={() => onDrop(conflict.edit_id)}>
                Drop
              </Button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
