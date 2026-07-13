import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TopPodcasts from './TopPodcasts'
import type { TopPodcast, TopPodcastsResponse } from '../api/types'

// The page calls these client functions directly — mock them so we can
// assert on the args passed to getTopPodcasts and avoid real network I/O.
vi.mock('../api/client', () => ({
  getTopPodcasts: vi.fn(),
  addPodcast: vi.fn(),
  resolvePodcast: vi.fn(),
}))

// Auth context — only ``user`` is read by this page. Provide a static
// stub so we don't need to wire up the real AuthProvider + fetch.
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
}))

// Toast — we don't assert on toasts here, just need ``useToast`` to resolve
// without needing the real provider tree.
vi.mock('../components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn(), dismissToast: vi.fn() }),
}))

import { getTopPodcasts } from '../api/client'

const mockGetTopPodcasts = getTopPodcasts as ReturnType<typeof vi.fn>

function row(overrides: Partial<TopPodcast> = {}): TopPodcast {
  return {
    rank: 1,
    name: 'Sample Show',
    artist: 'Sample Artist',
    rss_url: 'https://example.com/feed',
    apple_url: null,
    youtube_url: null,
    category: null,
    source_genre: null,
    is_following: false,
    podcast_slug: null,
    image_url: null,
    ...overrides,
  }
}

function response(
  rows: TopPodcast[],
  overrides: Partial<TopPodcastsResponse> = {},
): TopPodcastsResponse {
  return {
    status: 'ok',
    timestamp: '2026-05-11T00:00:00Z',
    region: 'gb',
    available_regions: ['gb', 'us'],
    available_categories: ['Comedy', 'History', 'Technology'],
    user_region: null,
    count: rows.length,
    top_podcasts: rows,
    ...overrides,
  }
}

// MemoryRouter (not BrowserRouter) so each render gets its own isolated
// history — filters now live in the URL, and a shared BrowserRouter would leak
// query state from one test into the next.
function renderPage(initialEntries: string[] = ['/top']) {
  const queryClient = new QueryClient({
    // gcTime 0 so switching a filter back to a previously-fetched value
    // re-issues the request instead of silently serving cache — lets the
    // tests assert on the args passed to getTopPodcasts.
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <TopPodcasts />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('TopPodcasts search', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTopPodcasts.mockResolvedValue(
      response([
        row({ rank: 1, name: 'The Rest Is History', artist: 'Goalhanger' }),
        row({ rank: 2, name: 'Crime Junkie', artist: 'audiochuck', rss_url: 'https://example.com/cj' }),
      ]),
    )
  })

  it('does not pass a q param on initial render', async () => {
    renderPage()

    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalled())
    // Signature: getTopPodcasts(region, limit, q)
    const [, , q] = mockGetTopPodcasts.mock.calls[0]
    expect(q).toBeUndefined()
  })

  it('debounces typing and sends the trimmed query as q', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalledTimes(1))

    const input = screen.getByLabelText(/search top podcasts/i)
    await user.type(input, ' hist ')

    // The debounce holds the request until ~250ms after the last keystroke.
    // We poll waitFor (default 1000ms timeout) for the debounced call.
    await waitFor(() => {
      const lastCall = mockGetTopPodcasts.mock.calls.at(-1)
      expect(lastCall?.[2]).toBe('hist')
    })

    // We should NOT have fired one call per keystroke — initial + one after
    // the debounce settles. Allow for at most a couple of extra invocations
    // from React StrictMode re-runs in dev, but not anywhere near 6.
    expect(mockGetTopPodcasts.mock.calls.length).toBeLessThan(4)
  })

  it('clears the query when the user empties the input', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalled())

    const input = screen.getByLabelText(/search top podcasts/i)
    await user.type(input, 'foo')
    await waitFor(() => {
      expect(mockGetTopPodcasts.mock.calls.at(-1)?.[2]).toBe('foo')
    })

    await user.clear(input)
    await waitFor(() => {
      // Empty string is coerced to undefined so the server's min_length=1
      // validator doesn't reject the request.
      expect(mockGetTopPodcasts.mock.calls.at(-1)?.[2]).toBeUndefined()
    })
  })

  it('shows a query-aware empty state when the search returns nothing', async () => {
    const user = userEvent.setup()
    // First call returns rows, second (after typing) returns empty.
    mockGetTopPodcasts
      .mockResolvedValueOnce(response([row({ rank: 1, name: 'Crime Junkie' })]))
      .mockResolvedValue({ ...response([]), count: 0 })

    renderPage()
    await waitFor(() => expect(screen.getByText('Crime Junkie')).toBeInTheDocument())

    const input = screen.getByLabelText(/search top podcasts/i)
    await user.type(input, 'zzzzz')

    await waitFor(() => {
      expect(screen.getByText(/no top podcasts match "zzzzz"/i)).toBeInTheDocument()
    })
  })

  it('clear button (×) resets the input and the query', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalled())

    const input = screen.getByLabelText(/search top podcasts/i) as HTMLInputElement
    await user.type(input, 'foo')
    expect(input.value).toBe('foo')

    const clearButton = await screen.findByRole('button', { name: /clear search/i })
    await user.click(clearButton)

    expect(input.value).toBe('')
    await waitFor(() => {
      expect(mockGetTopPodcasts.mock.calls.at(-1)?.[2]).toBeUndefined()
    })
  })
})

describe('TopPodcasts category filter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTopPodcasts.mockResolvedValue(
      response([
        row({ rank: 1, name: 'Tech Show', artist: 'TechCo' }),
      ]),
    )
  })

  it('populates the category dropdown from available_categories', async () => {
    renderPage()
    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalled())

    const select = (await screen.findByLabelText(/filter by category/i)) as HTMLSelectElement
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    // "All" plus the seeded categories from the mock response.
    expect(options).toEqual(['All', 'Comedy', 'History', 'Technology'])
  })

  it('passes the selected category to the API', async () => {
    const user = userEvent.setup()
    renderPage()
    // Wait for the response to render (options come from the payload).
    await screen.findByRole('option', { name: 'Technology' })

    const select = screen.getByLabelText(/filter by category/i)
    await user.selectOptions(select, 'Technology')

    await waitFor(() => {
      // Signature: getTopPodcasts(region, limit, q, category)
      const lastCall = mockGetTopPodcasts.mock.calls.at(-1)
      expect(lastCall?.[3]).toBe('Technology')
    })
  })

  it('clears category back to undefined when "All" is reselected', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('option', { name: 'Comedy' })

    const select = screen.getByLabelText(/filter by category/i)
    await user.selectOptions(select, 'Comedy')
    await waitFor(() => {
      expect(mockGetTopPodcasts.mock.calls.at(-1)?.[3]).toBe('Comedy')
    })

    await user.selectOptions(select, '')
    await waitFor(() => {
      expect(mockGetTopPodcasts.mock.calls.at(-1)?.[3]).toBeUndefined()
    })
  })

  it('shows a category-aware empty state', async () => {
    const user = userEvent.setup()
    mockGetTopPodcasts
      .mockResolvedValueOnce(response([row({ rank: 1, name: 'X' })]))
      .mockResolvedValue(response([]))

    renderPage()
    await waitFor(() => expect(screen.getByText('X')).toBeInTheDocument())

    await user.selectOptions(screen.getByLabelText(/filter by category/i), 'History')

    await waitFor(() => {
      expect(screen.getByText(/no top podcasts in history for this region/i)).toBeInTheDocument()
    })
  })

  it('combines q + category in the empty-state message', async () => {
    const user = userEvent.setup()
    mockGetTopPodcasts
      .mockResolvedValueOnce(response([row({ rank: 1, name: 'X' })]))
      .mockResolvedValue(response([]))

    renderPage()
    await waitFor(() => expect(screen.getByText('X')).toBeInTheDocument())

    await user.selectOptions(screen.getByLabelText(/filter by category/i), 'History')
    await user.type(screen.getByLabelText(/search top podcasts/i), 'foo')

    await waitFor(() => {
      expect(
        screen.getByText(/no top podcasts in history match "foo"/i),
      ).toBeInTheDocument()
    })
  })
})

// Filters live in the URL so the browser Back button restores them (and the
// scroll position) when returning from a podcast detail page.
describe('TopPodcasts URL-persisted filters', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTopPodcasts.mockResolvedValue(response([row({ rank: 1, name: 'Seeded Show' })]))
  })

  it('seeds region, category and q from the URL query string on mount', async () => {
    renderPage(['/top?region=us&category=Technology&q=hist'])

    await waitFor(() => expect(mockGetTopPodcasts).toHaveBeenCalled())
    // Signature: getTopPodcasts(region, limit, q, category)
    const [region, , q, category] = mockGetTopPodcasts.mock.calls[0]
    expect(region).toBe('us')
    expect(q).toBe('hist')
    expect(category).toBe('Technology')

    // The search input reflects the restored query.
    const input = screen.getByLabelText(/search top podcasts/i) as HTMLInputElement
    expect(input.value).toBe('hist')
  })
})
