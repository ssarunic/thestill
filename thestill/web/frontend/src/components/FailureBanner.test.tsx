import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import FailureBanner from './FailureBanner'

// Mirrors the server-side require_admin on POST /api/episodes/{id}/retry.
// Non-admins keep the failure info — only the action is admin-only.
let mockIsAdmin = true
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}))

vi.mock('../hooks/useApi', () => ({
  useRetryFailedEpisode: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
}))

function renderBanner() {
  return render(
    <FailureBanner
      episodeId="ep-1"
      failedAtStage="transcribe"
      failureReason="whisper exploded"
      failureType="fatal"
      failedAt="2026-07-01T00:00:00Z"
    />
  )
}

describe('FailureBanner admin gating', () => {
  beforeEach(() => {
    mockIsAdmin = true
  })

  it('shows the Retry button for admins', () => {
    renderBanner()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('hides Retry but keeps failure info for non-admins', () => {
    mockIsAdmin = false
    renderBanner()
    expect(screen.getByText(/processing failed \(fatal error\)/i)).toBeInTheDocument()
    expect(screen.getByText(/transcription/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^retry/i })).not.toBeInTheDocument()
  })
})
