import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MorningBriefingWidget from './MorningBriefingWidget'
import { ToastProvider } from './Toast'
import type { DigestPreviewResponse } from '../api/types'

vi.mock('../hooks/useApi', () => ({
  useMorningBriefingCount: vi.fn(),
  useLatestDigest: vi.fn(),
  useCreateMorningBriefing: vi.fn(),
}))

import {
  useMorningBriefingCount,
  useLatestDigest,
  useCreateMorningBriefing,
} from '../hooks/useApi'

const mockUseMorningBriefingCount = useMorningBriefingCount as ReturnType<typeof vi.fn>
const mockUseLatestDigest = useLatestDigest as ReturnType<typeof vi.fn>
const mockUseCreateMorningBriefing = useCreateMorningBriefing as ReturnType<typeof vi.fn>

function makeBriefingData(count: number): DigestPreviewResponse {
  return {
    status: 'ok',
    timestamp: '2026-01-01T00:00:00Z',
    episodes: Array.from({ length: count }, (_, i) => ({
      episode_id: `ep-${i}`,
      episode_title: `Episode ${i}`,
      episode_slug: `episode-${i}`,
      podcast_id: `pod-${i}`,
      podcast_title: `Pod ${i}`,
      podcast_slug: `pod-${i}`,
      state: 'summarized',
      pub_date: '2026-01-01T00:00:00Z',
    })),
    total_matching: count,
    criteria: {
      since_days: 7,
      max_episodes: 10,
      podcast_id: null,
      ready_only: true,
      exclude_digested: false,
    },
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ToastProvider>{children}</ToastProvider>
        </BrowserRouter>
      </QueryClientProvider>
    )
  }
}

describe('MorningBriefingWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders available episode count from morning briefing preview', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(25),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('25')).toBeInTheDocument()
    expect(screen.getByText('episodes ready for digest')).toBeInTheDocument()
  })

  it('shows loading state while the count is loading', () => {
    mockUseMorningBriefingCount.mockReturnValue({ data: null, isLoading: true })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('...')).toBeInTheDocument()
  })

  it('displays latest digest status when available', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(10),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({
      data: {
        digest: {
          id: 'digest-1',
          status: 'completed',
          episodes_total: 5,
          episodes_completed: 5,
        },
      },
    })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('(5/5)')).toBeInTheDocument()
  })

  it('calls createMorningBriefing when Quick Catch-Up is clicked', async () => {
    const user = userEvent.setup()
    const mockMutateAsync = vi.fn().mockResolvedValue({
      status: 'completed',
      timestamp: '2026-01-01T00:00:00Z',
      message: 'ok',
      digest_id: 'd-1',
      episodes_selected: 5,
    })

    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(10),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: mockMutateAsync,
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const button = screen.getByRole('button', { name: /quick catch-up/i })
    await user.click(button)

    // Server-configured defaults; the hook takes no arguments.
    expect(mockMutateAsync).toHaveBeenCalledWith()
  })

  it('disables the button when there are no pending episodes', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(0),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const button = screen.getByRole('button', { name: /quick catch-up/i })
    expect(button).toBeDisabled()
  })

  it('shows loading spinner when creating digest', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(10),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: true,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('Creating...')).toBeInTheDocument()
  })

  it('renders link to digests page', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(5),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const link = screen.getByRole('link', { name: /view all|digests/i })
    expect(link).toHaveAttribute('href', '/digests')
  })

  it('uses singular "episode" when count is 1', () => {
    mockUseMorningBriefingCount.mockReturnValue({
      data: makeBriefingData(1),
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateMorningBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('episode ready for digest')).toBeInTheDocument()
  })
})
