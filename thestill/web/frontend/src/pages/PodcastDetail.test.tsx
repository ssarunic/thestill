import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import PodcastDetail from './PodcastDetail'
import { ToastProvider } from '../components/Toast'

vi.mock('../hooks/useApi', () => ({
  usePodcast: vi.fn(),
  usePodcastEpisodesInfinite: vi.fn(() => ({
    data: { pages: [{ episodes: [], total: 0 }] },
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  })),
  useFollowPodcast: vi.fn(() => ({ mutate: vi.fn() })),
  useUnfollowPodcast: vi.fn(() => ({ mutate: vi.fn() })),
  useProcessingStageByEpisodeId: vi.fn(() => new Map()),
  useInvalidateEpisodesWhenRefreshSettles: vi.fn(),
}))
vi.stubGlobal(
  'IntersectionObserver',
  class {
    observe() {}
    disconnect() {}
    unobserve() {}
  },
)

import { usePodcast } from '../hooks/useApi'
const mockUsePodcast = usePodcast as ReturnType<typeof vi.fn>

function podcast(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    timestamp: '',
    podcast: {
      id: 'p1',
      index: 1,
      title: 'Prof G Markets',
      description: 'Markets, explained.',
      rss_url: 'https://example.com/rss',
      slug: 'prof-g-markets',
      image_url: null,
      primary_category: 'Business',
      primary_subcategory: 'Investing',
      secondary_category: null,
      secondary_subcategory: null,
      last_processed: '2026-09-01T00:00:00Z',
      episodes_count: 120,
      episodes_processed: 118,
      is_following: false,
      author: 'Prof G Media',
      explicit: true,
      website_url: 'https://www.profgmedia.com',
      is_complete: false,
      copyright: '© Prof G Media',
      ...overrides,
    },
  }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/podcasts/prof-g-markets']}>
          <Routes>
            <Route path="/podcasts/:podcastSlug" element={<PodcastDetail />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('PodcastDetail header (spec #76 phase 3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePodcast.mockReturnValue({ data: podcast(), isLoading: false, error: null })
  })

  it('renders the hero, a pill Follow action, a Website icon and the Details list', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'Prof G Markets' })).toBeInTheDocument()
    expect(screen.getByText('Business').closest('p')).toHaveTextContent('Investing')
    expect(screen.getByText('Business').closest('p')).toHaveTextContent('Explicit')
    expect(screen.getByText('By Prof G Media')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Follow' }).className).toContain('rounded-full')
    expect(screen.getByRole('link', { name: 'Website' })).toHaveAttribute('href', 'https://www.profgmedia.com')

    const details = screen.getByRole('region', { name: 'Details' })
    const terms = Array.from(details.querySelectorAll('dt')).map((el) => el.textContent)
    expect(terms).toEqual(['Author', 'Category', 'Episodes', 'Processed', 'Explicit', 'Website', 'Copyright'])
    expect(details).toHaveTextContent('Business › Investing')
    expect(details).toHaveTextContent('118')
  })

  it('shows the refresh spinner beside the Episodes heading while a refresh is pending (spec #74)', () => {
    mockUsePodcast.mockReturnValue({ data: podcast({ refresh_pending: true }), isLoading: false, error: null })
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Checking for new episodes…')
    mockUsePodcast.mockReturnValue({ data: podcast({ refresh_pending: true, episodes_count: 0 }), isLoading: false, error: null })
    renderPage()
    expect(screen.getAllByRole('status').at(-1)).toHaveTextContent('Loading episodes…')
  })

  it('offers Unfollow when following and omits the website action without a URL', () => {
    mockUsePodcast.mockReturnValue({ data: podcast({ is_following: true, website_url: null }), isLoading: false, error: null })
    renderPage()
    expect(screen.getByRole('button', { name: 'Unfollow' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Website' })).toBeNull()
  })
})
