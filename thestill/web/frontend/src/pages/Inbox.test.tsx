import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Inbox from './Inbox'
import type { Episode, InboxItem, InboxListResponse } from '../api/types'

vi.mock('../hooks/useApi', () => ({
  useInbox: vi.fn(),
}))

import { useInbox } from '../hooks/useApi'

const mockUseInbox = useInbox as ReturnType<typeof vi.fn>

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>{children}</BrowserRouter>
      </QueryClientProvider>
    )
  }
}

function episodeWith(state: Episode['state'], overrides: Partial<Episode> = {}): Episode {
  return {
    id: 'ep-1',
    podcast_index: 0,
    podcast_slug: 'sample',
    episode_index: 0,
    title: 'Sample Episode',
    slug: 'sample-episode',
    description: '',
    pub_date: null,
    audio_url: 'https://example.com/x.mp3',
    duration: null,
    duration_formatted: null,
    external_id: 'ext-1',
    state,
    transcript_available: false,
    summary_available: state === 'summarized',
    image_url: null,
    summary_preview: null,
    ...overrides,
  }
}

function inboxItem(overrides: Partial<InboxItem['entry']>, episode: Episode): InboxItem {
  return {
    entry: {
      id: 'i-1',
      user_id: 'u-1',
      episode_id: episode.id,
      source: 'import',
      state: 'unread',
      delivered_at: '2026-05-08T00:00:00Z',
      state_changed_at: null,
      ...overrides,
    },
    episode,
    podcast: { id: 'p-1', title: 'Sample Pod', slug: 'sample-pod', image_url: null },
  }
}

function inboxResponse(items: InboxItem[]): InboxListResponse {
  return {
    status: 'ok',
    timestamp: '2026-05-08T00:00:00Z',
    items,
    count: items.length,
    next_before: null,
  }
}

describe('Inbox progress badges', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it.each<[Episode['state'], string]>([
    ['discovered', 'Downloading…'],
    ['downloaded', 'Transcribing…'],
    ['transcribed', 'Cleaning…'],
    ['cleaned', 'Summarising…'],
  ])('renders %s as "%s"', (state, label) => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([inboxItem({}, episodeWith(state))]),
      isLoading: false,
      error: null,
    })
    render(<Inbox />, { wrapper: createWrapper() })
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('hides the progress pill once an episode is summarised', () => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([inboxItem({}, episodeWith('summarized'))]),
      isLoading: false,
      error: null,
    })
    render(<Inbox />, { wrapper: createWrapper() })
    expect(screen.queryByText(/Downloading|Transcribing|Cleaning|Summarising|Ready/)).toBeNull()
  })

  it('shows "Failed" for episodes flagged as failed regardless of state', () => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([
        inboxItem({}, episodeWith('downloaded', { is_failed: true })),
      ]),
      isLoading: false,
      error: null,
    })
    render(<Inbox />, { wrapper: createWrapper() })
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('marks imported rows with an "imported" label', () => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([
        inboxItem({ source: 'import' }, episodeWith('discovered')),
      ]),
      isLoading: false,
      error: null,
    })
    render(<Inbox />, { wrapper: createWrapper() })
    expect(screen.getByText('imported')).toBeInTheDocument()
  })
})

describe('Inbox empty state + import button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the empty-state import CTA when there are no deliveries', async () => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([]),
      isLoading: false,
      error: null,
    })
    render(<Inbox />, { wrapper: createWrapper() })
    expect(screen.getByText(/paste a link to import one/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Import episode/ })).toBeInTheDocument()
  })

  it('opens the import modal when the header button is clicked', async () => {
    mockUseInbox.mockReturnValue({
      data: inboxResponse([inboxItem({}, episodeWith('summarized'))]),
      isLoading: false,
      error: null,
    })
    const user = userEvent.setup()
    render(<Inbox />, { wrapper: createWrapper() })

    expect(screen.queryByRole('heading', { name: /import episode/i })).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Import$/ }))
    expect(screen.getByRole('heading', { name: /import episode/i })).toBeInTheDocument()
  })
})
