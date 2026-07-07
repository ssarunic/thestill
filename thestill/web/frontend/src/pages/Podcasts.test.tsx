import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Podcasts from './Podcasts'
import type { PodcastSummary, PodcastsResponse } from '../api/types'

// The page fetches through usePodcastsInfinite → getPodcasts. Mock the
// client function so we can assert on the q param and avoid network I/O.
vi.mock('../api/client', () => ({
  getPodcasts: vi.fn(),
}))

// The add-podcast modal pulls in toast/auth machinery we don't exercise here.
vi.mock('../components/AddPodcastModal', () => ({
  default: () => null,
}))

import { getPodcasts } from '../api/client'

const mockGetPodcasts = getPodcasts as ReturnType<typeof vi.fn>

function podcast(overrides: Partial<PodcastSummary> = {}): PodcastSummary {
  return {
    index: 1,
    title: 'Hard Fork',
    description: 'd',
    description_text: 'd',
    rss_url: 'https://example.com/feed',
    slug: 'hard-fork',
    image_url: null,
    last_processed: null,
    episodes_count: 10,
    episodes_processed: 5,
    ...overrides,
  }
}

function response(rows: PodcastSummary[], total = rows.length): PodcastsResponse {
  return {
    status: 'ok',
    timestamp: '2026-07-07T00:00:00Z',
    podcasts: rows,
    count: rows.length,
    total,
    offset: 0,
    limit: 12,
    has_more: false,
    next_offset: null,
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Podcasts />
      </BrowserRouter>
    </QueryClientProvider>,
  )
}

beforeAll(() => {
  // The page's infinite scroll observes a sentinel div; jsdom has no
  // IntersectionObserver, so provide an inert stub.
  vi.stubGlobal(
    'IntersectionObserver',
    vi.fn(() => ({ observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() })),
  )
})

describe('Podcasts search', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetPodcasts.mockResolvedValue(
      response([podcast(), podcast({ index: 2, title: 'The Daily', slug: 'the-daily' })]),
    )
  })

  it('does not pass a q param on initial render', async () => {
    renderPage()

    await waitFor(() => expect(mockGetPodcasts).toHaveBeenCalled())
    // Signature: getPodcasts(limit, offset, q)
    const [, , q] = mockGetPodcasts.mock.calls[0]
    expect(q).toBeUndefined()
    expect(await screen.findByText('Hard Fork')).toBeInTheDocument()
  })

  it('debounces typing and sends the trimmed query as q', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetPodcasts).toHaveBeenCalled())

    await user.type(screen.getByTestId('podcasts-search-input'), '  hard ')

    await waitFor(() => {
      const calls = mockGetPodcasts.mock.calls
      expect(calls[calls.length - 1][2]).toBe('hard')
    })
  })

  it('shows the match count while a filter is active', async () => {
    const user = userEvent.setup()
    mockGetPodcasts.mockResolvedValue(response([podcast()], 1))
    renderPage()

    await user.type(screen.getByTestId('podcasts-search-input'), 'hard')

    expect(await screen.findByText('1 podcast matching "hard"')).toBeInTheDocument()
  })

  it('shows a no-match state with a clear-filter action instead of the onboarding empty state', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetPodcasts).toHaveBeenCalled())

    mockGetPodcasts.mockResolvedValue(response([], 0))
    await user.type(screen.getByTestId('podcasts-search-input'), 'zzz')

    expect(await screen.findByText('No matches')).toBeInTheDocument()
    expect(screen.queryByText('No podcasts followed')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filter' }))
    await waitFor(() => {
      const calls = mockGetPodcasts.mock.calls
      expect(calls[calls.length - 1][2]).toBeUndefined()
    })
  })

  it('clearing the input via the × button resets to the unfiltered list', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetPodcasts).toHaveBeenCalled())

    await user.type(screen.getByTestId('podcasts-search-input'), 'daily')
    await waitFor(() => {
      const calls = mockGetPodcasts.mock.calls
      expect(calls[calls.length - 1][2]).toBe('daily')
    })

    // Two clear buttons exist (desktop + mobile input) — click the visible one.
    await user.click(screen.getAllByRole('button', { name: 'Clear search' })[0])
    await waitFor(() => {
      const calls = mockGetPodcasts.mock.calls
      expect(calls[calls.length - 1][2]).toBeUndefined()
    })
  })
})
