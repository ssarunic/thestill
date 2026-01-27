import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MorningBriefingWidget from './MorningBriefingWidget'
import { ToastProvider } from './Toast'

// Mock the useApi hooks
vi.mock('../hooks/useApi', () => ({
  useDashboardStats: vi.fn(),
  useLatestDigest: vi.fn(),
  useCreateDigest: vi.fn(),
}))

import { useDashboardStats, useLatestDigest, useCreateDigest } from '../hooks/useApi'

const mockUseDashboardStats = useDashboardStats as ReturnType<typeof vi.fn>
const mockUseLatestDigest = useLatestDigest as ReturnType<typeof vi.fn>
const mockUseCreateDigest = useCreateDigest as ReturnType<typeof vi.fn>

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ToastProvider>
            {children}
          </ToastProvider>
        </BrowserRouter>
      </QueryClientProvider>
    )
  }
}

describe('MorningBriefingWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders pending episode count from dashboard stats', () => {
    mockUseDashboardStats.mockReturnValue({
      data: {
        podcasts_tracked: 5,
        episodes_total: 100,
        episodes_processed: 75,
        episodes_pending: 25,
        pipeline: {},
      },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('25')).toBeInTheDocument()
    expect(screen.getByText('episodes ready to summarize')).toBeInTheDocument()
  })

  it('shows loading state when stats are loading', () => {
    mockUseDashboardStats.mockReturnValue({
      data: null,
      isLoading: true,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('...')).toBeInTheDocument()
  })

  it('displays latest digest status when available', () => {
    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 10 },
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
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('(5/5)')).toBeInTheDocument()
  })

  it('calls createDigest when Quick Catch-Up is clicked', async () => {
    const user = userEvent.setup()
    const mockMutateAsync = vi.fn().mockResolvedValue({
      status: 'completed',
      episodes_selected: 5,
    })

    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 10 },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({
      mutateAsync: mockMutateAsync,
      isPending: false,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const button = screen.getByRole('button', { name: /quick catch-up/i })
    await user.click(button)

    expect(mockMutateAsync).toHaveBeenCalledWith({
      since_days: 7,
      max_episodes: 10,
      ready_only: true,
      exclude_digested: true,
    })
  })

  it('disables button when there are no pending episodes', () => {
    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 0 },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const button = screen.getByRole('button', { name: /quick catch-up/i })
    expect(button).toBeDisabled()
  })

  it('shows loading spinner when creating digest', () => {
    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 10 },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: true,
    })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('Creating...')).toBeInTheDocument()
  })

  it('renders link to digests page', () => {
    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 5 },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    const link = screen.getByRole('link', { name: /view all/i })
    expect(link).toHaveAttribute('href', '/digests')
  })

  it('uses singular "episode" when count is 1', () => {
    mockUseDashboardStats.mockReturnValue({
      data: { episodes_pending: 1 },
      isLoading: false,
    })
    mockUseLatestDigest.mockReturnValue({ data: null })
    mockUseCreateDigest.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })

    render(<MorningBriefingWidget />, { wrapper: createWrapper() })

    expect(screen.getByText('episode ready to summarize')).toBeInTheDocument()
  })
})
