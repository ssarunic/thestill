import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Analytics from './Analytics'
import type { AnalyticsReport, AnalyticsResponse } from '../api/types'

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
