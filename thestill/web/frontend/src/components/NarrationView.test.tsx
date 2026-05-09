import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import NarrationView from './NarrationView'
import type { NarrationSummary } from '../api/types'

vi.mock('../hooks/useApi', () => ({
  useNarration: vi.fn(),
  useNarrateDigest: vi.fn(),
}))

import { useNarration, useNarrateDigest } from '../hooks/useApi'

const mockUseNarration = useNarration as ReturnType<typeof vi.fn>
const mockUseNarrateDigest = useNarrateDigest as ReturnType<typeof vi.fn>

function withQueryClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

function makeSummary(overrides: Partial<NarrationSummary> = {}): NarrationSummary {
  return {
    narration_id: 'digest-1-medium',
    slug: 'medium',
    target_duration_seconds: 300,
    actual_duration_seconds: 290,
    mode: 'narrated',
    fallback_reason: null,
    generated_at: '2026-05-08T07:00:00+00:00',
    schema_version: 'phase2',
    script_path: 'data/narrations/digest-1-medium.json',
    markdown_path: 'data/narrations/digest-1-medium.md',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUseNarrateDigest.mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
    error: null,
  })
})

describe('NarrationView', () => {
  it('renders the link-index fallback when no narration variants exist', () => {
    mockUseNarration.mockReturnValue({ data: null, isLoading: false })
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    expect(screen.getByText('LINK INDEX')).toBeInTheDocument()
    // Length switcher chips are still present so the user can request one.
    expect(screen.getByRole('button', { name: /Short/i })).toBeInTheDocument()
  })

  it('renders narrated markdown when a variant exists', () => {
    mockUseNarration.mockReturnValue({
      data: { id: 'digest-1-medium', script: {}, markdown: '# Briefing\n\nHello.' },
      isLoading: false,
    })
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[makeSummary()]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    expect(screen.getByRole('heading', { name: 'Briefing' })).toBeInTheDocument()
    expect(screen.queryByText('LINK INDEX')).not.toBeInTheDocument()
  })

  it('shows the fallback banner + link-index when narration mode is "fallback"', () => {
    mockUseNarration.mockReturnValue({ data: null, isLoading: false })
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[
            makeSummary({ mode: 'fallback', fallback_reason: 'word_budget_high' }),
          ]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    expect(screen.getByText(/Narration unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/word_budget_high/)).toBeInTheDocument()
    expect(screen.getByText('LINK INDEX')).toBeInTheDocument()
  })

  it('toggles between narrated view and link-index when the user clicks "Show link-index"', async () => {
    const user = userEvent.setup()
    mockUseNarration.mockReturnValue({
      data: { id: 'digest-1-medium', script: {}, markdown: '# Briefing\n' },
      isLoading: false,
    })
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[makeSummary()]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    expect(screen.getByRole('heading', { name: 'Briefing' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Show link-index/i }))
    expect(screen.getByText('LINK INDEX')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Briefing' })).not.toBeInTheDocument()
  })

  it('clicking a length chip that already exists swaps the displayed variant', async () => {
    const user = userEvent.setup()
    const markdownByNarrationId: Record<string, string> = {
      'digest-1-medium': '# Medium briefing',
      'digest-1-short': '# Short briefing',
    }
    mockUseNarration.mockImplementation((id: string | null) => ({
      data: id ? { id, script: {}, markdown: markdownByNarrationId[id] ?? '' } : null,
      isLoading: false,
    }))
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[
            makeSummary({ slug: 'short', narration_id: 'digest-1-short' }),
            makeSummary({ slug: 'medium', narration_id: 'digest-1-medium' }),
          ]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    // Default is medium (preferred preset).
    expect(screen.getByRole('heading', { name: 'Medium briefing' })).toBeInTheDocument()
    expect(mockUseNarration).toHaveBeenCalledWith('digest-1-medium')
    await user.click(screen.getByRole('button', { name: /^Short/ }))
    expect(mockUseNarrateDigest().mutateAsync).not.toHaveBeenCalled()
    expect(
      screen.getByRole('heading', { name: 'Short briefing' }),
    ).toBeInTheDocument()
  })

  it('clicking a length chip that does not exist triggers narrateDigest', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({
      narration_id: 'digest-1-long',
      digest_id: 'digest-1',
      slug: 'long',
      mode: 'narrated',
      target_duration_seconds: 600,
      actual_duration_seconds: 580,
      quote_count: 4,
      fallback_reason: null,
      script_path: 'data/narrations/digest-1-long.json',
      markdown_path: 'data/narrations/digest-1-long.md',
    })
    mockUseNarrateDigest.mockReturnValue({ mutateAsync, isPending: false, error: null })
    mockUseNarration.mockReturnValue({
      data: { id: 'digest-1-medium', script: {}, markdown: '# m' },
      isLoading: false,
    })
    render(
      withQueryClient(
        <NarrationView
          digestId="digest-1"
          narrations={[makeSummary({ slug: 'medium', narration_id: 'digest-1-medium' })]}
          linkIndexFallback={<div>LINK INDEX</div>}
        />,
      ),
    )
    await user.click(screen.getByRole('button', { name: /^Long/ }))
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        digestId: 'digest-1',
        request: { target_duration: 'long' },
      })
    })
  })
})
