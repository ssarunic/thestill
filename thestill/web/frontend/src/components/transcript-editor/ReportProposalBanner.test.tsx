import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReportProposalBanner from './ReportProposalBanner'
import { parseReportProposal } from './reportProposal'

// Spec #110 Phase 3 — the report deep link's staged proposal.

describe('parseReportProposal', () => {
  it('reads a complete proposal', () => {
    const params = new URLSearchParams('edit=1&report=r-1&segment=12&fix=wrong_speaker&value=Grace%20Hopper')
    expect(parseReportProposal(params)).toEqual({ reportId: 'r-1', segmentId: 12, fix: 'wrong_speaker', value: 'Grace Hopper' })
  })
  it('rejects an unknown fix or kind and tolerates a missing value', () => {
    expect(parseReportProposal(new URLSearchParams('report=r&segment=1&fix=bad_boundary'))).toBeNull()
    expect(parseReportProposal(new URLSearchParams('report=r&segment=1&fix=wrong_kind&value=jingle'))).toBeNull()
    expect(parseReportProposal(new URLSearchParams('report=r&segment=1&fix=wrong_words'))).toEqual({
      reportId: 'r',
      segmentId: 1,
      fix: 'wrong_words',
      value: null,
    })
    expect(parseReportProposal(new URLSearchParams('report=r&segment=x&fix=wrong_words'))).toBeNull()
  })
})

describe('ReportProposalBanner', () => {
  it('accepts a proposal with a value', async () => {
    const onAccept = vi.fn()
    render(
      <ReportProposalBanner
        proposal={{ reportId: 'r-1', segmentId: 12, fix: 'wrong_speaker', value: 'Grace Hopper' }}
        segmentMissing={false}
        busy={false}
        onAccept={onAccept}
        onDismiss={vi.fn()}
      />,
    )
    expect(screen.getByText('Report r-1')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(onAccept).toHaveBeenCalled()
  })

  it('cannot accept when the segment is gone or no fix was proposed', () => {
    const { rerender } = render(
      <ReportProposalBanner
        proposal={{ reportId: 'r-1', segmentId: 12, fix: 'wrong_words', value: 'better words' }}
        segmentMissing
        busy={false}
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    expect(screen.getByText(/no longer in this transcript/)).toBeInTheDocument()
    rerender(
      <ReportProposalBanner
        proposal={{ reportId: 'r-1', segmentId: 12, fix: 'wrong_words', value: null }}
        segmentMissing={false}
        busy={false}
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    expect(screen.getByText(/No fix was proposed/)).toBeInTheDocument()
  })
})
