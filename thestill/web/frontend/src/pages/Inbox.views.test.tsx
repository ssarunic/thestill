import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Inbox from './Inbox'
import type { ArrivingResponse, Episode, InboxItem, InboxListResponse } from '../api/types'

// Spec #88: All / Unread / Saved views and the "Arriving soon" strip. Mocks
// the API client so the real hooks and the URL binding run.
vi.mock('../api/client', () => ({
  getInbox: vi.fn(),
  getArriving: vi.fn(),
}))

vi.mock('../components/BriefingCard', () => ({
  default: () => <div data-testid="briefing-card" />,
}))

import { getArriving, getInbox } from '../api/client'

const mockGetInbox = getInbox as ReturnType<typeof vi.fn>
const mockGetArriving = getArriving as ReturnType<typeof vi.fn>

function episode(title: string, state: Episode['state'] = 'summarized'): Episode {
  return {
    id: `ep-${title}`,
    podcast_id: 'p1',
    external_id: `ext-${title}`,
    title,
    slug: title.toLowerCase().replace(/\s+/g, '-'),
    description: '',
    audio_url: 'https://cdn.example.com/a.mp3',
    pub_date: '2026-09-21T12:00:00Z',
    state,
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
      state: 'saved',
      delivered_at: '2026-09-21T12:00:00Z',
      state_changed_at: null,
    },
    episode: episode(title),
    podcast: { id: 'p1', title: '20VC', slug: '20vc', image_url: null },
  } as unknown as InboxItem
}

function inboxResponse(items: InboxItem[]): InboxListResponse {
  return { status: 'ok', timestamp: '', items, count: items.length, next_before: null }
}

function arriving(titles: string[], total = titles.length): ArrivingResponse {
  return {
    status: 'ok',
    timestamp: '',
    items: titles.map((t) => ({
      episode: episode(t, 'downloaded'),
      podcast: { id: 'p1', title: '20VC', slug: '20vc', image_url: null },
    })),
    count: titles.length,
    total,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

function renderPage(initialEntry = '/inbox') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/inbox"
            element={
              <>
                <Inbox />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function lastInboxCall() {
  const calls = mockGetInbox.mock.calls
  return calls[calls.length - 1][0] ?? {}
}

describe('Inbox views (spec #88)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetInbox.mockResolvedValue(inboxResponse([item('Inside Legora')]))
    mockGetArriving.mockResolvedValue(arriving([]))
  })

  it('defaults to All: no state filter, briefing shown', async () => {
    renderPage()

    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())
    expect(lastInboxCall().state).toBeUndefined()
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('briefing-card')).toBeInTheDocument()
  })

  it('switching to Saved sends state=saved, binds ?view=, and hides briefing and strip', async () => {
    mockGetArriving.mockResolvedValue(arriving(['On its way']))
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByTestId('arriving-soon')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Saved' }))

    await waitFor(() => expect(lastInboxCall().state).toBe('saved'))
    expect(screen.getByTestId('location')).toHaveTextContent('/inbox?view=saved')
    expect(await screen.findByText('1 saved')).toBeInTheDocument()
    expect(screen.queryByTestId('briefing-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('arriving-soon')).not.toBeInTheDocument()
  })

  it('restores the view from the URL and composes it with the search', async () => {
    renderPage('/inbox?view=unread&q=legora')

    await waitFor(() => expect(lastInboxCall()).toMatchObject({ state: 'unread', q: 'legora' }))
    expect(screen.getByRole('button', { name: 'Unread' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('switching view keeps q, and going back to All drops only view', async () => {
    const user = userEvent.setup()
    renderPage('/inbox?q=legora')
    await waitFor(() => expect(mockGetInbox).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'Unread' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/inbox?q=legora&view=unread')

    await user.click(screen.getByRole('button', { name: 'All' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/inbox\?q=legora$/)
  })

  it.each([
    ['unread', 'Nothing unread. Nice.'],
    ['saved', 'Nothing saved yet'],
  ])('empty %s view has its own empty state', async (view, heading) => {
    mockGetInbox.mockResolvedValue(inboxResponse([]))
    renderPage(`/inbox?view=${view}`)

    expect(await screen.findByText(heading)).toBeInTheDocument()
    expect(screen.queryByText('No deliveries yet')).not.toBeInTheDocument()
  })

  it('a failed Saved request shows the error, not the empty state', async () => {
    mockGetInbox.mockRejectedValue(new Error('API error: 500'))
    renderPage('/inbox?view=saved')

    expect(await screen.findByText('Error loading inbox')).toBeInTheDocument()
    expect(screen.queryByTestId('inbox-view-empty')).not.toBeInTheDocument()
  })
})

describe('Arriving soon (spec #88)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetInbox.mockResolvedValue(inboxResponse([item('Inside Legora')]))
  })

  it('lists in-flight episodes with their progress and the overflow count', async () => {
    mockGetArriving.mockResolvedValue(arriving(['Higgsfield story', 'Parallel agents'], 5))
    renderPage()

    const strip = await screen.findByTestId('arriving-soon')
    expect(strip).toHaveTextContent('Arriving soon')
    expect(strip).toHaveTextContent('Higgsfield story')
    expect(strip).toHaveTextContent('Transcribing…')
    expect(strip).toHaveTextContent('and 3 more')
    expect(screen.getByRole('link', { name: /Higgsfield story/ })).toHaveAttribute(
      'href',
      '/podcasts/20vc/episodes/higgsfield-story',
    )
  })

  it('labels progress from the active task, not the lagging episode state', async () => {
    // Dalston transcribes by URL, so the episode is still 'discovered'.
    const onDalston = { ...episode('On Dalston', 'discovered'), id: 'ep-dalston' }
    const waiting = { ...episode('Waiting', 'discovered'), id: 'ep-waiting' }
    const podcast = { id: 'p1', title: '20VC', slug: '20vc', image_url: null }
    mockGetArriving.mockResolvedValue({
      ...arriving([]),
      items: [
        { episode: onDalston, podcast, active_stage: 'transcribe', active_status: 'processing' },
        { episode: waiting, podcast, active_stage: 'download', active_status: 'pending' },
      ],
      count: 2,
      total: 2,
    })
    renderPage()

    const strip = await screen.findByTestId('arriving-soon')
    expect(within(strip).getByRole('link', { name: /On Dalston/ })).toHaveTextContent('Transcribing…')
    expect(within(strip).getByRole('link', { name: /Waiting/ })).toHaveTextContent('Queued')
    expect(strip).not.toHaveTextContent('Downloading…')
  })

  it('is absent when nothing is arriving', async () => {
    mockGetArriving.mockResolvedValue(arriving([]))
    renderPage()

    await screen.findByText('Inside Legora')
    await waitFor(() => expect(mockGetArriving).toHaveBeenCalled())
    expect(screen.queryByTestId('arriving-soon')).not.toBeInTheDocument()
  })

  it('is absent when the request fails, and the list still renders', async () => {
    mockGetArriving.mockRejectedValue(new Error('API error: 500'))
    renderPage()

    expect(await screen.findByText('Inside Legora')).toBeInTheDocument()
    await waitFor(() => expect(mockGetArriving).toHaveBeenCalled())
    expect(screen.queryByTestId('arriving-soon')).not.toBeInTheDocument()
  })

  it('is hidden while searching', async () => {
    mockGetArriving.mockResolvedValue(arriving(['On its way']))
    renderPage('/inbox?q=legora')

    await screen.findByText('Inside Legora')
    expect(screen.queryByTestId('arriving-soon')).not.toBeInTheDocument()
  })
})
