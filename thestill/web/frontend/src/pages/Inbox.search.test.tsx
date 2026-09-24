import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Inbox from './Inbox'
import type { Episode, InboxItem, InboxListResponse } from '../api/types'

// Spec #85 inbox search. Unlike Inbox.test.tsx (which mocks the React
// Query hooks), this file mocks the API client so the real hook, the URL
// binding and the debounce all run: the assertions are on what reaches
// ``getInbox``.
vi.mock('../api/client', () => ({
  getInbox: vi.fn(),
}))

vi.mock('../components/BriefingCard', () => ({
  default: () => <div data-testid="briefing-card" />,
}))

import { getInbox } from '../api/client'

const mockGetInbox = getInbox as ReturnType<typeof vi.fn>

function episode(title: string): Episode {
  return {
    id: `ep-${title}`,
    podcast_id: 'p1',
    external_id: `ext-${title}`,
    title,
    slug: title.toLowerCase().replace(/\s+/g, '-'),
    description: '',
    description_html: '',
    audio_url: 'https://cdn.example.com/a.mp3',
    pub_date: '2026-09-21T12:00:00Z',
    state: 'summarized',
    is_failed: false,
  } as unknown as Episode
}

function item(title: string): InboxItem {
  return {
    entry: {
      id: `entry-${title}`,
      user_id: 'u1',
      episode_id: `ep-${title}`,
      source: 'follow_new',
      state: 'unread',
      delivered_at: '2026-09-21T12:00:00Z',
      state_changed_at: null,
    },
    episode: episode(title),
    podcast: { id: 'p1', title: 'Andrej Karpathy', slug: 'andrej-karpathy', image_url: null },
  } as unknown as InboxItem
}

function response(items: InboxItem[]): InboxListResponse {
  return {
    status: 'ok',
    timestamp: '2026-09-24T00:00:00Z',
    items,
    count: items.length,
    next_before: null,
  }
}

function renderPage(initialEntry = '/inbox') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Inbox />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function lastCallOptions() {
  const calls = mockGetInbox.mock.calls
  return calls[calls.length - 1][0] ?? {}
}

describe('Inbox search (spec #85)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetInbox.mockResolvedValue(response([item('Deep Dive into LLMs like ChatGPT')]))
  })

  it('does not send q on initial render and shows the briefing card', async () => {
    renderPage()

    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())
    expect(lastCallOptions().q).toBeUndefined()
    expect(await screen.findByText('Deep Dive into LLMs like ChatGPT')).toBeInTheDocument()
    expect(screen.getByTestId('briefing-card')).toBeInTheDocument()
    expect(screen.getByText('1 delivered')).toBeInTheDocument()
  })

  it('debounces typing, sends the trimmed query as q, and switches the count copy', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())

    await user.type(screen.getByTestId('inbox-search-input'), '  karpathy ')

    await waitFor(() => expect(lastCallOptions().q).toBe('karpathy'))
    expect(await screen.findByText('1 matching')).toBeInTheDocument()
    expect(screen.queryByTestId('briefing-card')).not.toBeInTheDocument()
  })

  it('restores q from the URL on mount (Back navigation)', async () => {
    renderPage('/inbox?q=karpathy')

    await waitFor(() => expect(lastCallOptions().q).toBe('karpathy'))
    expect(screen.getByTestId('inbox-search-input')).toHaveValue('karpathy')
    expect(screen.queryByTestId('briefing-card')).not.toBeInTheDocument()
  })

  it('renders the no-match state with Clear search and Search everything', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())

    mockGetInbox.mockResolvedValue(response([]))
    await user.type(screen.getByTestId('inbox-search-input'), 'zzz')

    const empty = (await screen.findByText(/Nothing in your inbox matches/)).closest('div')!
    expect(screen.queryByText('No deliveries yet')).not.toBeInTheDocument()
    expect(within(empty).getByRole('link', { name: 'Search everything →' })).toHaveAttribute(
      'href',
      '/search?q=zzz',
    )

    await user.click(screen.getByTestId('inbox-search-clear'))

    // The unfiltered page comes back from the React Query cache (its key
    // is unchanged and still fresh), so assert on the rendered state.
    expect(screen.getByTestId('inbox-search-input')).toHaveValue('')
    expect(await screen.findByTestId('briefing-card')).toBeInTheDocument()
    expect(await screen.findByText('Deep Dive into LLMs like ChatGPT')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing in your inbox matches/)).not.toBeInTheDocument()
  })

  it('Escape clears the input', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())

    const input = screen.getByTestId('inbox-search-input')
    await user.type(input, 'karpathy')
    await waitFor(() => expect(lastCallOptions().q).toBe('karpathy'))

    await user.type(input, '{Escape}')

    expect(input).toHaveValue('')
    expect(await screen.findByTestId('briefing-card')).toBeInTheDocument()
    expect(await screen.findByText('1 delivered')).toBeInTheDocument()
  })
})
