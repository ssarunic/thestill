import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import PodcastDetail from './PodcastDetail'
import type { PodcastDetail as PodcastDetailRow, PodcastDetailResponse } from '../api/types'

// The page reads everything through the useApi hooks. Mock the hook module
// so we control the podcast payload directly and never touch react-query or
// the network; the episode list is empty so EpisodeCard stays out of scope.
vi.mock('../hooks/useApi', () => ({
  usePodcast: vi.fn(),
  usePodcastEpisodesInfinite: vi.fn(),
  useFollowPodcast: () => ({ mutate: vi.fn() }),
  useUnfollowPodcast: () => ({ mutate: vi.fn() }),
  useProcessingStageByEpisodeId: () => new Map(),
}))

// Toast — not asserted on; just needs ``useToast`` to resolve without the
// provider tree.
vi.mock('../components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn(), dismissToast: vi.fn() }),
}))

import { usePodcast, usePodcastEpisodesInfinite } from '../hooks/useApi'

const mockUsePodcast = usePodcast as ReturnType<typeof vi.fn>
const mockUseEpisodes = usePodcastEpisodesInfinite as ReturnType<typeof vi.fn>

function podcast(overrides: Partial<PodcastDetailRow> = {}): PodcastDetailRow {
  return {
    id: 'p1',
    index: 1,
    title: 'Hard Fork',
    description: 'd',
    rss_url: 'https://example.com/feed',
    slug: 'hard-fork',
    image_url: null,
    primary_category: null,
    primary_subcategory: null,
    secondary_category: null,
    secondary_subcategory: null,
    last_processed: '2026-07-07T00:00:00Z',
    episodes_count: 0,
    episodes_processed: 0,
    is_following: false,
    ...overrides,
  }
}

function response(row: PodcastDetailRow): PodcastDetailResponse {
  return { status: 'ok', timestamp: '2026-07-07T00:00:00Z', podcast: row }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/podcasts/hard-fork']}>
      <Routes>
        <Route path="/podcasts/:podcastSlug" element={<PodcastDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeAll(() => {
  // jsdom implements neither; the page calls both on mount.
  vi.stubGlobal('scrollTo', vi.fn())
  vi.stubGlobal(
    'IntersectionObserver',
    vi.fn(() => ({ observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() })),
  )
})

beforeEach(() => {
  vi.clearAllMocks()
  mockUseEpisodes.mockReturnValue({
    data: { pages: [{ episodes: [], total: 0, next_offset: null }] },
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  })
})

describe('PodcastDetail store links', () => {
  it('renders Apple Podcasts and YouTube links when the podcast carries them', () => {
    mockUsePodcast.mockReturnValue({
      data: response(
        podcast({
          website_url: 'https://hardfork.example',
          apple_url: 'https://podcasts.apple.com/us/podcast/hard-fork/id123',
          youtube_url: 'https://www.youtube.com/@hardfork',
        }),
      ),
      isLoading: false,
      error: null,
    })

    renderPage()

    const apple = screen.getByRole('link', { name: /apple podcasts/i })
    expect(apple).toHaveAttribute('href', 'https://podcasts.apple.com/us/podcast/hard-fork/id123')
    expect(apple).toHaveAttribute('target', '_blank')
    expect(apple).toHaveAttribute('rel', 'noopener noreferrer')

    const youtube = screen.getByRole('link', { name: /youtube/i })
    expect(youtube).toHaveAttribute('href', 'https://www.youtube.com/@hardfork')

    // The existing website link is untouched.
    expect(screen.getByRole('link', { name: /hardfork\.example/i })).toHaveAttribute(
      'href',
      'https://hardfork.example',
    )
  })

  it('renders only the Apple link when the YouTube URL is null', () => {
    mockUsePodcast.mockReturnValue({
      data: response(
        podcast({
          apple_url: 'https://podcasts.apple.com/us/podcast/hard-fork/id123',
          youtube_url: null,
        }),
      ),
      isLoading: false,
      error: null,
    })

    renderPage()

    expect(screen.getByRole('link', { name: /apple podcasts/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /youtube/i })).not.toBeInTheDocument()
  })

  it('renders nothing extra when the podcast has no store links', () => {
    mockUsePodcast.mockReturnValue({
      data: response(podcast({ apple_url: null, youtube_url: null })),
      isLoading: false,
      error: null,
    })

    renderPage()

    expect(screen.queryByRole('link', { name: /apple podcasts/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /youtube/i })).not.toBeInTheDocument()
    // Sanity: the header itself rendered (we are not looking at the skeleton).
    expect(screen.getByRole('heading', { level: 1, name: 'Hard Fork' })).toBeInTheDocument()
  })
})
