import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import NarrationStatsTile from './NarrationStatsTile'
import type { NarrationDashboardStats } from '../api/types'

vi.mock('../hooks/useApi', () => ({
  useNarrationDashboardStats: vi.fn(),
}))

import { useNarrationDashboardStats } from '../hooks/useApi'

const mockHook = useNarrationDashboardStats as ReturnType<typeof vi.fn>

function withProviders(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  )
}

function makeStats(overrides: Partial<NarrationDashboardStats> = {}): NarrationDashboardStats {
  return {
    status: 'ok',
    timestamp: '2026-05-08T07:00:00Z',
    total_runs: 6,
    fallback_count: 1,
    fallback_rate: 0.166,
    avg_actual_duration_seconds: 286.4,
    avg_target_duration_seconds: 300,
    avg_latency_ms: 4280,
    latest: {
      narration_id: 'briefing-001-medium',
      briefing_id: 'briefing-001',
      generated_at: '2026-05-08T07:00:00+00:00',
      mode: 'narrated',
      fallback_reason: null,
      target_duration_seconds: 300,
      actual_duration_seconds: 290,
      latency_ms: 4280,
    },
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('NarrationStatsTile', () => {
  it('renders nothing while loading', () => {
    mockHook.mockReturnValue({ data: undefined, isLoading: true, error: null })
    const { container } = render(withProviders(<NarrationStatsTile />))
    // Skeleton block exists (the animate-pulse container).
    expect(container.querySelector('.animate-pulse')).toBeTruthy()
  })

  it('renders nothing when zero runs', () => {
    mockHook.mockReturnValue({
      data: makeStats({ total_runs: 0, latest: null }),
      isLoading: false,
      error: null,
    })
    const { container } = render(withProviders(<NarrationStatsTile />))
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing on error (silent)', () => {
    mockHook.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('boom'),
    })
    const { container } = render(withProviders(<NarrationStatsTile />))
    expect(container.firstChild).toBeNull()
  })

  it('renders headline metrics when runs exist', () => {
    mockHook.mockReturnValue({ data: makeStats(), isLoading: false, error: null })
    render(withProviders(<NarrationStatsTile />))
    expect(screen.getByText('Narration health')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument() // total_runs
    expect(screen.getByText('16.6%')).toBeInTheDocument() // fallback_rate
    expect(screen.getByText('1 of 6')).toBeInTheDocument() // counts
    expect(screen.getByText('4m 46s')).toBeInTheDocument() // avg_actual
    expect(screen.getByText('4.3s')).toBeInTheDocument() // avg_latency
  })

  it('links to the briefing viewer for the latest run', () => {
    mockHook.mockReturnValue({ data: makeStats(), isLoading: false, error: null })
    render(withProviders(<NarrationStatsTile />))
    const link = screen.getByRole('link', { name: /View latest/ })
    expect(link).toHaveAttribute('href', '/briefings/briefing-001')
  })

  it('shows fallback note when latest run failed', () => {
    mockHook.mockReturnValue({
      data: makeStats({
        latest: {
          narration_id: 'briefing-002-short',
          briefing_id: 'briefing-002',
          generated_at: '2026-05-08T07:00:00+00:00',
          mode: 'fallback',
          fallback_reason: 'word_budget_high',
          target_duration_seconds: 180,
          actual_duration_seconds: 0,
          latency_ms: 4500,
        },
      }),
      isLoading: false,
      error: null,
    })
    render(withProviders(<NarrationStatsTile />))
    expect(screen.getByText(/fell back to link-index/)).toBeInTheDocument()
    expect(screen.getByText(/word_budget_high/)).toBeInTheDocument()
  })

  it('hides the deep-link when briefing_id is missing (legacy artefact)', () => {
    mockHook.mockReturnValue({
      data: makeStats({
        latest: {
          narration_id: 'legacy-medium',
          briefing_id: null,
          generated_at: '2026-05-08T07:00:00+00:00',
          mode: 'narrated',
          fallback_reason: null,
          target_duration_seconds: 300,
          actual_duration_seconds: 290,
          latency_ms: 4280,
        },
      }),
      isLoading: false,
      error: null,
    })
    render(withProviders(<NarrationStatsTile />))
    // Headline metrics still render; the "View latest" link is gone.
    expect(screen.getByText('Narration health')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /View latest/ })).toBeNull()
  })

  it('color-codes the fallback rate (low → emerald, high → red)', () => {
    mockHook.mockReturnValue({
      data: makeStats({ fallback_rate: 0.02 }),
      isLoading: false,
      error: null,
    })
    const { rerender, container } = render(withProviders(<NarrationStatsTile />))
    expect(container.querySelector('.text-emerald-700')).toBeTruthy()

    mockHook.mockReturnValue({
      data: makeStats({ fallback_rate: 0.25 }),
      isLoading: false,
      error: null,
    })
    rerender(withProviders(<NarrationStatsTile />))
    expect(container.querySelector('.text-red-700')).toBeTruthy()
  })
})
