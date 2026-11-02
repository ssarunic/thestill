import Button from '../Button'
import type { ReportFix, ReportProposal } from './reportProposal'

// Spec #110 Phase 3 — a spec #86 report deep link opens the editor with its
// proposal staged on the anchored segment. Accept writes the op with the
// report id; Dismiss leaves the editor open on the segment.

const LABELS: Record<ReportFix, string> = {
  wrong_speaker: 'Wrong speaker',
  wrong_words: 'Wrong words',
  wrong_kind: 'Wrong segment kind',
}

interface ReportProposalBannerProps {
  proposal: ReportProposal
  /** The segment is no longer in the transcript (a re-clean moved it). */
  segmentMissing: boolean
  busy: boolean
  onAccept: () => void
  onDismiss: () => void
}

export default function ReportProposalBanner({ proposal, segmentMissing, busy, onAccept, onDismiss }: ReportProposalBannerProps) {
  const canAccept = !segmentMissing && proposal.value !== null
  return (
    <div
      role="region"
      aria-label="Report proposal"
      className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-900"
    >
      <span className="font-medium">Report {proposal.reportId}</span>
      <span>
        {LABELS[proposal.fix]}
        {proposal.value !== null && (
          <>
            {' → '}
            <span className="font-medium">{proposal.value}</span>
          </>
        )}
      </span>
      {segmentMissing && <span className="text-xs text-indigo-800">The reported segment is no longer in this transcript.</span>}
      {!segmentMissing && proposal.value === null && (
        <span className="text-xs text-indigo-800">No fix was proposed; edit the segment below.</span>
      )}
      <div className="ml-auto flex items-center gap-2">
        {canAccept && (
          <Button variant="primary" size="sm" onClick={onAccept} isLoading={busy}>
            Accept
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={onDismiss} disabled={busy}>
          Dismiss
        </Button>
      </div>
    </div>
  )
}
