import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Analytics from './Analytics'
import type { AnalyticsPodcastStat, AnalyticsReport, AnalyticsResponse } from '../api/types'

const podcast = (over: Partial<AnalyticsPodcastStat> & { title: string }): AnalyticsPodcastStat => ({
  podcast_id: over.title,
  slug: over.title.toLowerCase().replace(/ /g, '-'),
  category: 'Technology',
  followers: 0,
  new_follows: 0,
  follower_readers: 0,
  readers: 0,
  readers_previous: 0,
  listeners: 0,
  completions: 0,
  public_views: 0,
  change: '—',
  ...over,
})

// Spec #95 — mocks the API client so the real hook and URL binding run.
vi.mock('../api/client', () => ({ getAnalytics: vi.fn() }))

import { getAnalytics } from '../api/client'
const mockGetAnalytics = getAnalytics as ReturnType<typeof vi.fn>

const STEP_LABELS: [string, string][] = [
  ['follow_24h', 'Followed a show within 24 h'],
  ['follow_7d', 'Followed a show within 7 days'],
  ['briefing_generated', 'First briefing within 7 days'],
  ['briefing_opened', 'Opened first briefing within 7 days'],
  ['reads_3_first_week', 'Read 3+ episodes in week 1'],
]

const steps = (eligible: number, reached: number) =>
  STEP_LABELS.map(
    ([key, label]) => ({
      key,
      label,
      horizon_hours: key === 'follow_24h' ? 24 : 168,
      eligible,
      reached,
      pct: eligible ? reached / eligible : null,
      median_hours: reached ? 2 : null,
    }),
  )

const REPORT: AnalyticsReport = {
  generated_at: '2026-10-14T12:00:00Z',
  range: '28d',
  window: { start: '2026-09-17T00:00:00Z', end: '2026-10-14T12:00:00Z', days: 28 },
  signups: {
    total: 12,
    by_source: { episode: 9, before_tracking: 3 },
    days: [{ day: '2026-10-14', total: 12, by_source: { episode: 9, before_tracking: 3 } }],
  },
  activation: {
    overall: { source: 'all', signups: 12, steps: steps(10, 4) },
    by_source: [
      { source: 'episode', signups: 9, steps: steps(8, 4) },
      { source: 'before_tracking', signups: 3, steps: steps(2, 0) },
    ],
  },
  cohorts: {
    weeks: [1, 2, 4, 8],
    rows: [
      {
        week_start: '2026-08-03',
        size: 7,
        cells: [
          { week: 1, eligible: 7, active: 3, pct: 3 / 7 },
          { week: 2, eligible: 7, active: 2, pct: 2 / 7 },
          { week: 4, eligible: 7, active: 1, pct: 1 / 7 },
          { week: 8, eligible: 0, active: 0, pct: null },
        ],
      },
    ],
  },
  engagement: {
    active_users: 6,
    reads: 30,
    reads_per_active_user_per_week: 1.25,
    briefings_generated: 8,
    briefings_opened: 6,
    opened_ratio: 0.75,
    follows: 14,
    follows_per_active_user: 2.33,
  },
  content: {
    podcasts: [
      podcast({ title: 'Hard Fork', readers: 9, readers_previous: 7, change: '+2', followers: 12, follower_readers: 6, new_follows: 3, public_views: 31 }),
      podcast({ title: 'Quiet Show', readers: 1, change: 'new', followers: 20, follower_readers: 1, category: null }),
      podcast({ title: 'Falling', readers: 6, readers_previous: 8, change: '\u22122', followers: 2, follower_readers: 2 }),
    ],
    episodes: [
      {
        episode_id: 'e1',
        slug: 'the-ai-episode',
        title: 'The AI Episode',
        podcast_slug: 'hard-fork',
        podcast_title: 'Hard Fork',
        published_at: '2026-10-10T00:00:00Z',
        imported: false,
        readers: 6,
        listeners: 3,
        completions: 1,
        public_views: 12,
      },
      {
        episode_id: 'e2',
        slug: 'a-talk',
        title: 'A Talk',
        podcast_slug: 'imports-youtube',
        podcast_title: 'YouTube imports',
        published_at: null,
        imported: true,
        readers: 1,
        listeners: 0,
        completions: 0,
        public_views: 0,
      },
    ],
    categories: [
      { category: 'Technology', shows: 4, shows_read: 3, followers: 15, readers: 10, readers_previous: 12, public_views: 44, change: '\u22122' },
      { category: 'Uncategorised', shows: 1, shows_read: 1, followers: 20, readers: 1, readers_previous: 0, public_views: 0, change: 'new' },
    ],
    notes: ['A reader opened an episode from the inbox or listened to it.'],
  },
  notes: ['Admin accounts and the single-user local account are excluded.'],
}

const available = (report: AnalyticsReport = REPORT): AnalyticsResponse => ({
  status: 'ok',
  timestamp: '',
  available: true,
  ...report,
})

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

function renderPage(initialEntry = '/analytics') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/analytics"
            element={
              <>
                <Analytics />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Analytics page (spec #95)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAnalytics.mockResolvedValue(available())
  })

  it('renders the four sections with counts beside every percentage', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Signups' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Activation' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Retention' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Engagement' })).toBeInTheDocument()
    expect(mockGetAnalytics).toHaveBeenCalledWith('28d')

    const activation = screen.getByRole('heading', { name: 'Activation' }).closest('section')!
    const firstStep = within(activation).getByRole('row', { name: /Followed a show within 24 h/ })
    expect(firstStep).toHaveTextContent('40.0% (4/10)')
    expect(within(activation).getByText('Episode page (9)')).toBeInTheDocument()
    expect(within(activation).getByText('Before tracking (3)')).toBeInTheDocument()
    expect(screen.getByText('Admin accounts and the single-user local account are excluded.')).toBeInTheDocument()
  })

  it('greys out groups under five users instead of hiding them', async () => {
    renderPage()
    const activation = (await screen.findByRole('heading', { name: 'Activation' })).closest('section')!
    const row = within(activation).getByRole('row', { name: /Followed a show within 24 h/ })
    const small = within(row).getByText('0.0%').closest('[data-small]')
    expect(small).toHaveClass('opacity-60')
    expect(within(row).getByText('40.0%').closest('[data-small]')).toBeNull()
  })

  it('leaves retention cells blank until the week has passed', async () => {
    renderPage()
    const retention = (await screen.findByRole('heading', { name: 'Retention' })).closest('section')!
    const row = within(retention).getByRole('row', { name: /Aug 3/ })
    const cells = within(row).getAllByRole('cell')
    expect(cells[0]).toHaveTextContent('7')
    expect(cells[1]).toHaveTextContent('42.9% (3/7)')
    expect(cells[4]).toHaveTextContent('')
  })

  it('keeps the range in the URL, omitting the default', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Signups' })
    await userEvent.setup().click(screen.getByRole('button', { name: '7 days' }))
    await waitFor(() => expect(mockGetAnalytics).toHaveBeenLastCalledWith('7d'))
    expect(screen.getByTestId('location')).toHaveTextContent('/analytics?range=7d')
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')

    await userEvent.setup().click(screen.getByRole('button', { name: '28 days' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/analytics$/))
  })

  it('falls back to 28 days for an unknown range in the URL', async () => {
    renderPage('/analytics?range=1y')
    await screen.findByRole('heading', { name: 'Signups' })
    expect(mockGetAnalytics).toHaveBeenCalledWith('28d')
  })

  it('says analytics is off when the server reports it unavailable', async () => {
    mockGetAnalytics.mockResolvedValue({ status: 'ok', timestamp: '', available: false })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Analytics is off' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Date range' })).not.toBeInTheDocument()
  })

  it('shows an error instead of an empty dashboard when the request fails', async () => {
    mockGetAnalytics.mockRejectedValue(new Error('API error: 500 Internal Server Error'))
    renderPage()
    expect(await screen.findByText('Error loading analytics')).toBeInTheDocument()
  })
})

describe('Analytics content section (spec #103)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAnalytics.mockResolvedValue(available())
  })

  const contentSection = async () => (await screen.findByRole('heading', { name: 'Content' })).closest('section')!
  const titles = (table: HTMLElement) =>
    within(table)
      .getAllByRole('rowheader')
      .map((th) => within(th).getByRole('link').textContent)

  it('renders podcasts, episodes and categories with change against the previous window', async () => {
    renderPage()
    const section = await contentSection()
    const podcasts = within(section).getByRole('group', { name: 'Podcasts' })
    expect(titles(podcasts)).toEqual(['Hard Fork', 'Falling', 'Quiet Show'])
    expect(within(podcasts).getByRole('row', { name: /Hard Fork/ })).toHaveTextContent('+2')
    expect(within(podcasts).getByRole('row', { name: /Falling/ })).toHaveTextContent('\u22122')
    const quiet = within(podcasts).getByRole('row', { name: /Quiet Show/ })
    expect(quiet).toHaveTextContent('new')
    expect(quiet).toHaveTextContent('Uncategorised')
    expect(within(podcasts).getByRole('row', { name: /Hard Fork/ })).toHaveTextContent('50.0% (6/12)')

    const episodes = within(section).getByRole('group', { name: 'Episodes' })
    expect(titles(episodes)).toEqual(['The AI Episode', 'A Talk'])
    expect(within(episodes).getByRole('row', { name: /A Talk/ })).toHaveTextContent('imported')

    const categories = within(section).getByRole('group', { name: 'Categories' })
    expect(within(categories).getByRole('row', { name: /Technology/ })).toHaveTextContent('\u22122')
    expect(within(categories).getByRole('row', { name: /Uncategorised/ })).toHaveTextContent('new')
    expect(within(section).getByText('A reader opened an episode from the inbox or listened to it.')).toBeInTheDocument()
  })

  it('shows public views in each table', async () => {
    renderPage()
    const section = await contentSection()
    const lastCell = (group: string, row: RegExp) =>
      within(within(section).getByRole('group', { name: group }).querySelector('table')!)
        .getByRole('row', { name: row })
        .lastElementChild?.textContent
    expect(within(section).getAllByRole('columnheader', { name: 'Public views' })).toHaveLength(3)
    expect(lastCell('Podcasts', /Hard Fork/)).toBe('31')
    expect(lastCell('Episodes', /The AI Episode/)).toBe('12')
    expect(lastCell('Categories', /Technology/)).toBe('44')
  })

  it('links titles to the signed-in podcast and episode pages', async () => {
    renderPage()
    const section = await contentSection()
    expect(within(section).getByRole('link', { name: 'Hard Fork' })).toHaveAttribute('href', '/podcasts/hard-fork')
    expect(within(section).getByRole('link', { name: 'The AI Episode' })).toHaveAttribute(
      'href',
      '/podcasts/hard-fork/episodes/the-ai-episode',
    )
  })

  it('greys the figures of rows under five readers, keeping the title readable', async () => {
    renderPage()
    const podcasts = within(await contentSection()).getByRole('group', { name: 'Podcasts' })
    const quiet = within(podcasts).getByRole('row', { name: /Quiet Show/ })
    expect(quiet).toHaveAttribute('data-small')
    expect(quiet).toHaveClass('[&>td]:opacity-60')
    expect(within(quiet).getByText(/fewer than 5 readers/)).toHaveClass('sr-only')
    expect(within(podcasts).getByRole('row', { name: /Hard Fork/ })).not.toHaveAttribute('data-small')
  })

  it('sorts podcasts by followers on a header click', async () => {
    renderPage()
    const podcasts = within(await contentSection()).getByRole('group', { name: 'Podcasts' })
    const followers = within(podcasts).getByRole('button', { name: 'Followers' })
    expect(followers.closest('th')).toHaveAttribute('aria-sort', 'none')
    await userEvent.setup().click(followers)
    expect(titles(podcasts)).toEqual(['Quiet Show', 'Hard Fork', 'Falling'])
    expect(followers.closest('th')).toHaveAttribute('aria-sort', 'descending')
  })

  it('shows an exact top 20 for either sort from the union the server sends', async () => {
    // 20 shows lead on readers; a 21st has few readers but the most followers.
    const leaders = Array.from({ length: 20 }, (_, i) => podcast({ title: `Show ${i}`, readers: 30 - i, followers: 1 }))
    const followed = podcast({ title: 'Followed', readers: 1, followers: 99 })
    mockGetAnalytics.mockResolvedValue(available({ ...REPORT, content: { ...REPORT.content, podcasts: [...leaders, followed] } }))
    renderPage()
    const podcasts = within(await contentSection()).getByRole('group', { name: 'Podcasts' })
    expect(titles(podcasts)).toHaveLength(20)
    expect(titles(podcasts)).not.toContain('Followed')
    await userEvent.setup().click(within(podcasts).getByRole('button', { name: 'Followers' }))
    expect(titles(podcasts)).toHaveLength(20)
    expect(titles(podcasts)[0]).toBe('Followed')
  })
})
