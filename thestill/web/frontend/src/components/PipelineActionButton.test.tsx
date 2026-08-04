import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import PipelineActionButton from './PipelineActionButton'

// Admin gating mirrors the server-side require_admin on the pipeline
// command endpoints — flip this per test.
let mockIsAdmin = true
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}))

vi.mock('../hooks/useApi', () => ({
  useQueuePipelineTask: () => ({ mutate: vi.fn(), isPending: false }),
  useRunPipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelPipeline: () => ({ mutate: vi.fn() }),
}))

function renderButton(episodeState = 'discovered') {
  return render(
    <PipelineActionButton
      podcastSlug="show"
      episodeSlug="ep"
      episodeId="ep-1"
      episodeState={episodeState}
      tasks={[]}
    />
  )
}

describe('PipelineActionButton admin gating', () => {
  beforeEach(() => {
    mockIsAdmin = true
  })

  it('renders the next-stage action for admins', () => {
    renderButton('discovered')
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument()
  })

  it('renders nothing for non-admins', () => {
    mockIsAdmin = false
    const { container } = renderButton('discovered')
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing once the episode is summarized (any role)', () => {
    const { container } = renderButton('summarized')
    expect(container.firstChild).toBeNull()
  })
})
