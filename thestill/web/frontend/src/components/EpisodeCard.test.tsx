import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import EpisodeCard from './EpisodeCard'
import type { Episode } from '../api/types'

// Pipeline state and failure details are operator information: admins get
// the stage badge, failure reason and the details modal with Retry
// (mirroring require_admin on POST /api/episodes/{id}/retry); everyone
// else sees a neutral "Unavailable" pill.
let mockIsAdmin = true
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}))

vi.mock('../hooks/useApi', () => ({
  useRetryFailedEpisode: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
}))

function failedEpisode(): Episode {
  return {
    id: 'ep-1',
    podcast_index: 1,
    podcast_slug: 'show',
    episode_index: 1,
    title: 'Broken Episode',
    slug: 'broken-episode',
    description: 'desc',
    pub_date: '2026-07-01T00:00:00Z',
    audio_url: 'https://example.com/a.mp3',
    duration: 60,
    duration_formatted: '1:00',
    external_id: 'x-1',
    state: 'downsampled',
    transcript_available: false,
    summary_available: false,
    image_url: null,
    summary_preview: null,
    is_failed: true,
    failed_at_stage: 'transcribe',
    failure_reason: 'whisper exploded',
    failure_type: 'fatal',
    failed_at: '2026-07-01T01:00:00Z',
  }
}

function renderCard(episode: Episode = failedEpisode()) {
  render(
    <MemoryRouter>
      <EpisodeCard episode={episode} isSelected={false} />
    </MemoryRouter>
  )
}

describe('EpisodeCard admin gating', () => {
  beforeEach(() => {
    mockIsAdmin = true
  })

  it('shows stage, reason and Retry in the failure modal for admins', async () => {
    renderCard()
    expect(screen.getByText('Downsampled')).toBeInTheDocument()
    expect(screen.getByText(/whisper exploded/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /view details/i }))
    expect(screen.getByRole('button', { name: /^retry/i })).toBeInTheDocument()
  })

  it('shows non-admins only a neutral Unavailable pill', () => {
    mockIsAdmin = false
    renderCard()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Failed')).not.toBeInTheDocument()
    expect(screen.queryByText('Downsampled')).not.toBeInTheDocument()
    expect(screen.queryByText(/whisper exploded/)).not.toBeInTheDocument()
    expect(screen.queryByText(/failed at/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /view details/i })).not.toBeInTheDocument()
  })

  it('shows non-admins no pipeline state pill on a ready episode', () => {
    mockIsAdmin = false
    renderCard({ ...failedEpisode(), state: 'summarized', is_failed: false, failure_type: null, failed_at_stage: null, failure_reason: null })
    expect(screen.queryByText('Ready')).not.toBeInTheDocument()
    expect(screen.queryByText('Unavailable')).not.toBeInTheDocument()
  })
})
