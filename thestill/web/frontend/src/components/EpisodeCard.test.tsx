import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import EpisodeCard from './EpisodeCard'
import type { Episode } from '../api/types'

// Mirrors the server-side require_admin on POST /api/episodes/{id}/retry:
// the failure-details modal keeps its info for everyone, but only admins
// get the Retry action.
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

async function openFailureModal() {
  render(
    <MemoryRouter>
      <EpisodeCard episode={failedEpisode()} isSelected={false} />
    </MemoryRouter>
  )
  await userEvent.click(screen.getByRole('button', { name: /view details/i }))
}

describe('EpisodeCard failure modal admin gating', () => {
  beforeEach(() => {
    mockIsAdmin = true
  })

  it('shows Retry in the failure modal for admins', async () => {
    await openFailureModal()
    expect(screen.getByRole('button', { name: /^retry/i })).toBeInTheDocument()
  })

  it('hides Retry but keeps the modal for non-admins', async () => {
    mockIsAdmin = false
    await openFailureModal()
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^retry/i })).not.toBeInTheDocument()
  })
})
