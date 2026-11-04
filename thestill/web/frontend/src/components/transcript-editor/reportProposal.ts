// Spec #110 Phase 3 — the report deep-link contract:
// ``?view=transcript&edit=1&report=<id>&segment=<segment id>&fix=…[&value=…]``.
// The spec #86 reports table does not exist yet; this is the half the
// editor owns (open on the segment, stage the proposal, write the op with
// the report id).
import type { SegmentKind } from '../../api/types'

export type ReportFix = 'wrong_speaker' | 'wrong_words' | 'wrong_kind'

export interface ReportProposal {
  reportId: string
  segmentId: number
  fix: ReportFix
  /** The proposed speaker name, corrected text or kind, when the report carried one. */
  value: string | null
}

const KINDS: SegmentKind[] = ['content', 'filler', 'ad_break', 'music', 'intro', 'outro']

export function parseReportProposal(params: URLSearchParams): ReportProposal | null {
  const reportId = params.get('report')
  const segment = Number(params.get('segment'))
  const fix = params.get('fix')
  if (!reportId || !Number.isInteger(segment) || !fix) return null
  if (fix !== 'wrong_speaker' && fix !== 'wrong_words' && fix !== 'wrong_kind') return null
  const value = params.get('value')
  if (fix === 'wrong_kind' && value && !(KINDS as string[]).includes(value)) return null
  return { reportId, segmentId: segment, fix, value: value || null }
}
