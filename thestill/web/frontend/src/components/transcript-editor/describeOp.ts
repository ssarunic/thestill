// Spec #110 — one-line descriptions of op-log rows for the conflicts list.
import type { TranscriptEdit } from '../../api/types'

const OP_LABELS: Record<string, string> = {
  'speaker.rename_label': 'Rename speaker',
  'speaker.merge_labels': 'Merge speakers',
  'segment.set_speaker': 'Change speaker',
  'segment.set_text': 'Edit text',
  'segment.set_kind': 'Change kind',
  'segment.split': 'Split segment',
  'segment.merge': 'Merge segments',
  revert: 'Undo',
}

export function describeOp(edit: Pick<TranscriptEdit, 'op' | 'anchor' | 'args'>): string {
  const label = OP_LABELS[edit.op] ?? edit.op
  switch (edit.op) {
    case 'speaker.rename_label':
      return `${label}: ${String(edit.anchor.label)} → ${String(edit.args.name)}`
    case 'speaker.merge_labels':
      return `${label}: ${String(edit.anchor.label)} into ${String(edit.args.into_label)}`
    case 'segment.set_speaker':
      return `${label}: ${String('label' in edit.args && edit.args.label ? edit.args.label : edit.args.name)}`
    case 'segment.set_kind':
      return `${label}: ${String(edit.args.kind)}`
    default:
      return label
  }
}
