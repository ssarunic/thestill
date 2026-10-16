import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Inbox from './Inbox'
import type { Episode, InboxItem, ListeningSummary, ResumableItem } from '../api/types'
import { listeningCache } from '../contexts/listening/listeningCache'

// Spec #90: listening progress on inbox rows and the Continue listening strip.
vi.mock('../api/client', () => ({
  getInbox: vi.fn(),
  getArriving: vi.fn(),
}))
vi.mock('../api/listening', () => ({
  listInProgress: vi.fn(),
}))
vi.mock('../components/BriefingCard', () => ({
  default: () => <div data-testid="briefing-card" />,
}))
const play = vi.fn()
vi.mock('../contexts/PlayerContext', () => ({
  usePlayer: () => ({ play }),
}))

import { getArriving, getInbox } from '../api/client'
import { listInProgress } from '../api/listening'

const mockGetInbox = vi.mocked(getInbox)
const mockGetArriving = vi.mocked(getArriving)
const mockListInProgress = vi.mocked(listInProgress)

const progress = (position: number, overrides: Partial<ListeningSummary> = {}): ListeningSummary => ({
  position_seconds: position,
  duration_seconds: 3600,
  completed_at: null,
  updated_at: '2026-10-16T09:00:00Z',
  ...overrides,
})

function row(title: string, listening: ListeningSummary | null): InboxItem {
  return {
    entry: {
      id: `entry-${title}`,
      user_id: 'u1',
      episode_id: `ep-${title}`,
      source: 'follow_new',
      state: 'read',
      delivered_at: '2026-09-21T12:00:00Z',
      state_changed_at: null,
    },
    episode: {
      id: `ep-${title}`,
      title,
      slug: title.toLowerCase(),
      audio_url: 'https://cdn.example.com/a.mp3',
      pub_date: '2026-09-21T12:00:00Z',
      state: 'summarized',
      duration: 3600,
      is_failed: false,
    } as unknown as Episode,
    podcast: { id: 'p1', title: '20VC', slug: '20vc', image_url: null },
    listening,
  }
}

function resumable(title: string, position: number): ResumableItem {
  return {
    listening: progress(position),
    episode: {
      id: `ep-${title}`,
      slug: title.toLowerCase(),
      title,
      podcast_id: 'p1',
      podcast_slug: '20vc',
      podcast_title: '20VC',
      audio_url: 'https://cdn.example.com/a.mp3',
      image_url: null,
      podcast_image_url: null,
      duration: 3600,
      playback: null,
    },
  }
}

function renderPage(initialEntry = '/inbox') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/inbox" element={<Inbox />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetInbox.mockResolvedValue({
    status: 'ok',
    timestamp: '',
    items: [row('Halfway', progress(1680)), row('Finished', progress(0, { completed_at: '2026-10-15T09:00:00Z' })), row('Fresh', null)],
    count: 3,
    next_before: null,
  })
  mockGetArriving.mockResolvedValue({ status: 'ok', timestamp: '', items: [], count: 0, total: 0 })
  mockListInProgress.mockResolvedValue([resumable('Halfway', 1680)])
})

describe('Inbox rows (spec #90)', () => {
  it('show time left or Played in the meta line', async () => {
    renderPage()
    const halfway = (await screen.findByText('Halfway')).closest('li')!
    expect(within(halfway).getByText('32 min left')).toBeInTheDocument()
    const finished = screen.getByText('Finished').closest('li')!
    expect(within(finished).getByText('Played')).toBeInTheDocument()
    const fresh = screen.getByText('Fresh').closest('li')!
    expect(within(fresh).queryByText(/left|Played/)).toBeNull()
  })
})

describe('Continue listening strip (spec #90)', () => {
  it('lists unfinished episodes and resumes one in place', async () => {
    renderPage()
    const strip = await screen.findByTestId('continue-listening')
    const button = within(strip).getByRole('button', { name: 'Resume Halfway, 32 min left' })
    await userEvent.setup().click(button)
    expect(play).toHaveBeenCalledWith(expect.objectContaining({ episodeId: 'ep-Halfway', podcastSlug: '20vc' }))
    // play() reads the position synchronously: the cache knew it first.
    expect(listeningCache.get('ep-Halfway')?.position_seconds).toBe(1680)
  })

  it('is hidden when nothing is in progress', async () => {
    mockListInProgress.mockResolvedValue([])
    renderPage()
    await screen.findByText('Halfway')
    await waitFor(() => expect(mockListInProgress).toHaveBeenCalled())
    expect(screen.queryByTestId('continue-listening')).toBeNull()
  })

  it('is hidden while ?q= filters the inbox and on state-filtered views', async () => {
    renderPage('/inbox?q=legora')
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())
    expect(screen.queryByTestId('continue-listening')).toBeNull()
    expect(mockListInProgress).not.toHaveBeenCalled()
  })

  it('is hidden on the Saved view', async () => {
    renderPage('/inbox?view=saved')
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())
    expect(screen.queryByTestId('continue-listening')).toBeNull()
  })
})
