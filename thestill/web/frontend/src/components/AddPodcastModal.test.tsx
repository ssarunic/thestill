import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AddPodcastModal, { parseAddInput } from './AddPodcastModal'
import type { TopPodcast, TopPodcastsResponse } from '../api/types'

vi.mock('../api/client', () => ({
  addPodcast: vi.fn(),
  getTopPodcasts: vi.fn(),
}))

import { addPodcast, getTopPodcasts } from '../api/client'

const mockAddPodcast = addPodcast as ReturnType<typeof vi.fn>
const mockGetTopPodcasts = getTopPodcasts as ReturnType<typeof vi.fn>

function row(overrides: Partial<TopPodcast>): TopPodcast {
  return {
    rank: 1,
    name: 'Sample',
    artist: 'Sampler',
    rss_url: 'https://example.com/feed',
    apple_url: null,
    youtube_url: null,
    category: null,
    source_genre: null,
    is_following: false,
    ...overrides,
  }
}

function response(rows: TopPodcast[]): TopPodcastsResponse {
  return {
    status: 'ok',
    timestamp: '2026-04-27T00:00:00Z',
    region: 'us',
    available_regions: ['us', 'gb'],
    user_region: 'us',
    count: rows.length,
    top_podcasts: rows,
  }
}

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

describe('parseAddInput', () => {
  it('classifies empty string as empty', () => {
    expect(parseAddInput('')).toEqual({ kind: 'empty' })
  })
  it('classifies whitespace-only as empty', () => {
    expect(parseAddInput('   ')).toEqual({ kind: 'empty' })
  })
  it('classifies single character as empty (below 2-char threshold)', () => {
    expect(parseAddInput('r')).toEqual({ kind: 'empty' })
  })
  it('classifies two characters as a query', () => {
    expect(parseAddInput('re')).toEqual({ kind: 'query', value: 're' })
  })
  it('classifies http URL as url', () => {
    expect(parseAddInput('http://example.com')).toEqual({
      kind: 'url',
      value: 'http://example.com',
    })
  })
  it('classifies https URL as url', () => {
    expect(parseAddInput('https://feeds.example.com/x')).toEqual({
      kind: 'url',
      value: 'https://feeds.example.com/x',
    })
  })
  it('classifies non-http schemes as url', () => {
    expect(parseAddInput('rss://feed.example')).toEqual({
      kind: 'url',
      value: 'rss://feed.example',
    })
  })
  it('does not classify a typed name with dots as URL', () => {
    expect(parseAddInput('feeds.example.com/x')).toEqual({
      kind: 'query',
      value: 'feeds.example.com/x',
    })
  })
  it('does not classify a typed name as URL', () => {
    expect(parseAddInput('rest is')).toEqual({ kind: 'query', value: 'rest is' })
  })
  it('trims whitespace before classifying', () => {
    expect(parseAddInput('  hello  ')).toEqual({ kind: 'query', value: 'hello' })
  })
})

describe('AddPodcastModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('does not render when closed', () => {
    render(<AddPodcastModal isOpen={false} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    expect(screen.queryByRole('heading', { name: /follow a podcast/i })).toBeNull()
  })

  it('shows the top chart on open with Following badge for already-followed rows', async () => {
    mockGetTopPodcasts.mockResolvedValue(
      response([
        row({ rank: 1, name: 'The Daily', is_following: false }),
        row({
          rank: 2,
          name: 'Crime Junkie',
          rss_url: 'https://example.com/junkie',
          is_following: true,
        }),
      ]),
    )

    render(<AddPodcastModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(screen.getByText('The Daily')).toBeInTheDocument()
      expect(screen.getByText('Crime Junkie')).toBeInTheDocument()
    })

    expect(screen.getByText('Follow')).toBeInTheDocument()
    const following = screen.getByText('Following ✓')
    expect(following).toBeInTheDocument()
    expect(following.closest('button')).toBeDisabled()
  })

  it('flips a Follow button to Following ✓ on successful add', async () => {
    mockGetTopPodcasts.mockResolvedValue(
      response([row({ rank: 1, name: 'The Daily', is_following: false })]),
    )
    mockAddPodcast.mockResolvedValue({
      status: 'started',
      message: 'started',
      task_type: 'add',
    })

    const user = userEvent.setup()
    const onClose = vi.fn()

    render(<AddPodcastModal isOpen={true} onClose={onClose} />, {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(screen.getByText('The Daily')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Follow' }))

    await waitFor(() => {
      expect(screen.getByText('Following ✓')).toBeInTheDocument()
    })
    expect(mockAddPodcast).toHaveBeenCalledWith({ url: 'https://example.com/feed' })
    // Modal stays open after a list-Follow.
    expect(onClose).not.toHaveBeenCalled()
  })

  it('switches to URL paste mode when input contains a scheme', async () => {
    mockGetTopPodcasts.mockResolvedValue(response([]))

    const user = userEvent.setup()

    render(<AddPodcastModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    await user.type(screen.getByRole('textbox'), 'https://feeds.example.com/x.xml')

    await waitFor(() => {
      expect(screen.getByText('Add this feed')).toBeInTheDocument()
    })
    expect(screen.getByText('https://feeds.example.com/x.xml')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument()
  })

  it('fetches with the typed query after debounce window', async () => {
    mockGetTopPodcasts.mockResolvedValue(response([]))

    const user = userEvent.setup()

    render(<AddPodcastModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    // Initial empty-state fetch on open.
    await waitFor(() => {
      expect(mockGetTopPodcasts).toHaveBeenCalled()
    })

    await user.type(screen.getByRole('textbox'), 'rest')

    // Eventually the search call fires with the typed query.
    // (region, limit, q, signal)
    await waitFor(() => {
      const calledWithQuery = mockGetTopPodcasts.mock.calls.some(
        (call) => call[2] === 'rest',
      )
      expect(calledWithQuery).toBe(true)
    })
  })

  it('navigates to /settings via the Change region link, closing the modal first', async () => {
    mockGetTopPodcasts.mockResolvedValue(response([row({ rank: 1, name: 'The Daily' })]))

    const user = userEvent.setup()
    const onClose = vi.fn()

    render(<AddPodcastModal isOpen={true} onClose={onClose} />, {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(screen.getByText('Change region')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Change region'))

    expect(onClose).toHaveBeenCalled()
  })
})
