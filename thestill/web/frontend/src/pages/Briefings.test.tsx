import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Briefings from './Briefings'
import { createBriefing, createPendingBriefing, createInProgressBriefing, createFailedBriefing } from '../test/mocks'

// Mock the useApi hooks
vi.mock('../hooks/useApi', () => ({
  useBriefings: vi.fn(),
  useCreateBriefing: vi.fn(),
  useDeleteBriefing: vi.fn(),
  usePreviewBriefing: vi.fn(),
}))

import { useBriefings, useCreateBriefing, useDeleteBriefing, usePreviewBriefing } from '../hooks/useApi'

const mockUseBriefings = useBriefings as ReturnType<typeof vi.fn>
const mockUseCreateBriefing = useCreateBriefing as ReturnType<typeof vi.fn>
const mockUseDeleteBriefing = useDeleteBriefing as ReturnType<typeof vi.fn>
const mockUsePreviewBriefing = usePreviewBriefing as ReturnType<typeof vi.fn>

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
          {children}
        </BrowserRouter>
      </QueryClientProvider>
    )
  }
}

describe('Briefings Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Default mock implementations
    mockUseCreateBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })
    mockUseDeleteBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })
    mockUsePreviewBriefing.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: null,
      reset: vi.fn(),
    })
  })

  describe('Loading state', () => {
    it('shows loading message while fetching briefings', () => {
      mockUseBriefings.mockReturnValue({
        data: null,
        isLoading: true,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getByText('Loading briefings...')).toBeInTheDocument()
    })
  })

  describe('Error state', () => {
    it('shows error message when fetch fails', () => {
      mockUseBriefings.mockReturnValue({
        data: null,
        isLoading: false,
        error: new Error('Network error'),
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getByText(/Error loading briefings/)).toBeInTheDocument()
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  describe('Empty state', () => {
    it('shows empty state message when no briefings', () => {
      mockUseBriefings.mockReturnValue({
        data: { briefings: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getByText('No briefings yet')).toBeInTheDocument()
      expect(screen.getByText('Create your first briefing to get started')).toBeInTheDocument()
    })
  })

  describe('Briefing list', () => {
    it('renders list of briefings', () => {
      const briefings = [
        createBriefing({ id: '1' }),
        createBriefing({ id: '2', status: 'partial', episodes_failed: 1 }),
      ]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 2 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getAllByText(/Briefing from/)).toHaveLength(2)
    })

    it('shows correct status badge colors', () => {
      const briefings = [
        createBriefing({ id: '1', status: 'completed' }),
        createPendingBriefing({ id: '2' }),
        createFailedBriefing({ id: '3' }),
      ]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 3 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      // "Completed" and "Failed" appear in both the briefing-card badge and
      // the info box at the bottom of the page. "Pending" only appears in
      // the badge. Assert at least one match for each so the test tolerates
      // the info-box duplication.
      expect(screen.getAllByText('Completed').length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText('Pending')).toBeInTheDocument()
      expect(screen.getAllByText('Failed').length).toBeGreaterThanOrEqual(1)
    })
  })

  describe('Progress indicator for active briefings', () => {
    it('shows progress bar for pending briefing', () => {
      const briefings = [createPendingBriefing({ id: '1', episodes_total: 5, episodes_completed: 0 })]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getByText('Processing episodes...')).toBeInTheDocument()
      expect(screen.getByText('0 of 5 episodes completed')).toBeInTheDocument()
    })

    it('shows progress bar for in_progress briefing', () => {
      const briefings = [createInProgressBriefing({ id: '1', episodes_total: 3, episodes_completed: 1 })]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.getByText('Processing episodes...')).toBeInTheDocument()
      expect(screen.getByText('1 of 3 episodes completed')).toBeInTheDocument()
    })

    it('does not show progress bar for completed briefing', () => {
      const briefings = [createBriefing({ id: '1', status: 'completed' })]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      expect(screen.queryByText('Processing episodes...')).not.toBeInTheDocument()
    })
  })

  describe('Stats cards', () => {
    it('shows correct counts in stats cards', () => {
      const briefings = [
        createBriefing({ id: '1', status: 'completed' }),
        createBriefing({ id: '2', status: 'completed' }),
        createBriefing({ id: '3', status: 'partial' }),
        createFailedBriefing({ id: '4' }),
      ]

      mockUseBriefings.mockReturnValue({
        data: { briefings, total: 4 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      // Scope each assertion to its stat card so we don't collide with the
      // identical count (1) shared by Partial and Failed.
      const totalCard = screen.getByText('Total Briefings').parentElement!
      expect(within(totalCard).getByText('4')).toBeInTheDocument()

      const completedCard = screen
        .getAllByText('Completed')
        .map((el) => el.parentElement)
        .find((el) => el?.querySelector('.text-2xl') !== null)!
      expect(within(completedCard).getByText('2')).toBeInTheDocument()

      const partialCard = screen
        .getAllByText('Partial')
        .map((el) => el.parentElement)
        .find((el) => el?.querySelector('.text-2xl') !== null)!
      expect(within(partialCard).getByText('1')).toBeInTheDocument()

      const failedCard = screen
        .getAllByText('Failed')
        .map((el) => el.parentElement)
        .find((el) => el?.querySelector('.text-2xl') !== null)!
      expect(within(failedCard).getByText('1')).toBeInTheDocument()
    })
  })

  describe('Create briefing modal', () => {
    it('opens modal when New Briefing button is clicked', async () => {
      const user = userEvent.setup()

      mockUseBriefings.mockReturnValue({
        data: { briefings: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /new briefing/i }))

      expect(screen.getByText('Create New Briefing')).toBeInTheDocument()
      expect(screen.getByText('Generate a briefing from your processed podcast episodes')).toBeInTheDocument()
    })

    it('closes modal when Cancel is clicked', async () => {
      const user = userEvent.setup()

      mockUseBriefings.mockReturnValue({
        data: { briefings: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /new briefing/i }))
      await user.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByText('Create New Briefing')).not.toBeInTheDocument()
    })
  })

  describe('Delete confirmation', () => {
    it('shows delete confirmation when Delete is clicked', async () => {
      const user = userEvent.setup()

      mockUseBriefings.mockReturnValue({
        data: { briefings: [createBriefing({ id: '1' })], total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))

      expect(screen.getByRole('button', { name: /yes/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /no/i })).toBeInTheDocument()
    })

    it('cancels delete when No is clicked', async () => {
      const user = userEvent.setup()

      mockUseBriefings.mockReturnValue({
        data: { briefings: [createBriefing({ id: '1' })], total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))
      await user.click(screen.getByRole('button', { name: /no/i }))

      // Should be back to single Delete button
      expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /yes/i })).not.toBeInTheDocument()
    })

    it('calls delete mutation when Yes is clicked', async () => {
      const user = userEvent.setup()
      const mockDeleteAsync = vi.fn().mockResolvedValue({})

      mockUseBriefings.mockReturnValue({
        data: { briefings: [createBriefing({ id: 'briefing-123' })], total: 1 },
        isLoading: false,
        error: null,
      })
      mockUseDeleteBriefing.mockReturnValue({
        mutateAsync: mockDeleteAsync,
        isPending: false,
      })

      render(<Briefings />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))
      await user.click(screen.getByRole('button', { name: /yes/i }))

      expect(mockDeleteAsync).toHaveBeenCalledWith('briefing-123')
    })
  })
})
