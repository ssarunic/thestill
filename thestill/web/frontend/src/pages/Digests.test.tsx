import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Digests from './Digests'
import { createDigest, createPendingDigest, createInProgressDigest, createFailedDigest } from '../test/mocks'

// Mock the useApi hooks
vi.mock('../hooks/useApi', () => ({
  useDigests: vi.fn(),
  useCreateDigest: vi.fn(),
  useDeleteDigest: vi.fn(),
  usePreviewDigest: vi.fn(),
}))

import { useDigests, useCreateDigest, useDeleteDigest, usePreviewDigest } from '../hooks/useApi'

const mockUseDigests = useDigests as ReturnType<typeof vi.fn>
const mockUseCreateDigest = useCreateDigest as ReturnType<typeof vi.fn>
const mockUseDeleteDigest = useDeleteDigest as ReturnType<typeof vi.fn>
const mockUsePreviewDigest = usePreviewDigest as ReturnType<typeof vi.fn>

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

describe('Digests Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Default mock implementations
    mockUseCreateDigest.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })
    mockUseDeleteDigest.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })
    mockUsePreviewDigest.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: null,
      reset: vi.fn(),
    })
  })

  describe('Loading state', () => {
    it('shows loading message while fetching digests', () => {
      mockUseDigests.mockReturnValue({
        data: null,
        isLoading: true,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText('Loading digests...')).toBeInTheDocument()
    })
  })

  describe('Error state', () => {
    it('shows error message when fetch fails', () => {
      mockUseDigests.mockReturnValue({
        data: null,
        isLoading: false,
        error: new Error('Network error'),
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText(/Error loading digests/)).toBeInTheDocument()
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  describe('Empty state', () => {
    it('shows empty state message when no digests', () => {
      mockUseDigests.mockReturnValue({
        data: { digests: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText('No digests yet')).toBeInTheDocument()
      expect(screen.getByText('Create your first digest to get started')).toBeInTheDocument()
    })
  })

  describe('Digest list', () => {
    it('renders list of digests', () => {
      const digests = [
        createDigest({ id: '1' }),
        createDigest({ id: '2', status: 'partial', episodes_failed: 1 }),
      ]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 2 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getAllByText(/Digest from/)).toHaveLength(2)
    })

    it('shows correct status badge colors', () => {
      const digests = [
        createDigest({ id: '1', status: 'completed' }),
        createPendingDigest({ id: '2' }),
        createFailedDigest({ id: '3' }),
      ]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 3 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText('Completed')).toBeInTheDocument()
      expect(screen.getByText('Pending')).toBeInTheDocument()
      expect(screen.getByText('Failed')).toBeInTheDocument()
    })
  })

  describe('Progress indicator for active digests', () => {
    it('shows progress bar for pending digest', () => {
      const digests = [createPendingDigest({ id: '1', episodes_total: 5, episodes_completed: 0 })]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText('Processing episodes...')).toBeInTheDocument()
      expect(screen.getByText('0 of 5 episodes completed')).toBeInTheDocument()
    })

    it('shows progress bar for in_progress digest', () => {
      const digests = [createInProgressDigest({ id: '1', episodes_total: 3, episodes_completed: 1 })]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.getByText('Processing episodes...')).toBeInTheDocument()
      expect(screen.getByText('1 of 3 episodes completed')).toBeInTheDocument()
    })

    it('does not show progress bar for completed digest', () => {
      const digests = [createDigest({ id: '1', status: 'completed' })]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      expect(screen.queryByText('Processing episodes...')).not.toBeInTheDocument()
    })
  })

  describe('Stats cards', () => {
    it('shows correct counts in stats cards', () => {
      const digests = [
        createDigest({ id: '1', status: 'completed' }),
        createDigest({ id: '2', status: 'completed' }),
        createDigest({ id: '3', status: 'partial' }),
        createFailedDigest({ id: '4' }),
      ]

      mockUseDigests.mockReturnValue({
        data: { digests, total: 4 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      // Total digests
      expect(screen.getByText('4')).toBeInTheDocument()
      // Completed (2)
      expect(screen.getByText('2')).toBeInTheDocument()
      // Partial (1)
      expect(screen.getByText('1')).toBeInTheDocument()
    })
  })

  describe('Create digest modal', () => {
    it('opens modal when New Digest button is clicked', async () => {
      const user = userEvent.setup()

      mockUseDigests.mockReturnValue({
        data: { digests: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /new digest/i }))

      expect(screen.getByText('Create New Digest')).toBeInTheDocument()
      expect(screen.getByText('Generate a digest from your processed podcast episodes')).toBeInTheDocument()
    })

    it('closes modal when Cancel is clicked', async () => {
      const user = userEvent.setup()

      mockUseDigests.mockReturnValue({
        data: { digests: [], total: 0 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /new digest/i }))
      await user.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByText('Create New Digest')).not.toBeInTheDocument()
    })
  })

  describe('Delete confirmation', () => {
    it('shows delete confirmation when Delete is clicked', async () => {
      const user = userEvent.setup()

      mockUseDigests.mockReturnValue({
        data: { digests: [createDigest({ id: '1' })], total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))

      expect(screen.getByRole('button', { name: /yes/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /no/i })).toBeInTheDocument()
    })

    it('cancels delete when No is clicked', async () => {
      const user = userEvent.setup()

      mockUseDigests.mockReturnValue({
        data: { digests: [createDigest({ id: '1' })], total: 1 },
        isLoading: false,
        error: null,
      })

      render(<Digests />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))
      await user.click(screen.getByRole('button', { name: /no/i }))

      // Should be back to single Delete button
      expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /yes/i })).not.toBeInTheDocument()
    })

    it('calls delete mutation when Yes is clicked', async () => {
      const user = userEvent.setup()
      const mockDeleteAsync = vi.fn().mockResolvedValue({})

      mockUseDigests.mockReturnValue({
        data: { digests: [createDigest({ id: 'digest-123' })], total: 1 },
        isLoading: false,
        error: null,
      })
      mockUseDeleteDigest.mockReturnValue({
        mutateAsync: mockDeleteAsync,
        isPending: false,
      })

      render(<Digests />, { wrapper: createWrapper() })

      await user.click(screen.getByRole('button', { name: /delete/i }))
      await user.click(screen.getByRole('button', { name: /yes/i }))

      expect(mockDeleteAsync).toHaveBeenCalledWith('digest-123')
    })
  })
})
