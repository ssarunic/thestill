import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import BriefingDetail from './BriefingDetail'

vi.mock('../hooks/useApi', () => ({
  useBriefing: vi.fn(),
  useBriefingEpisodes: vi.fn(),
  useBriefingScript: vi.fn(() => ({ data: { markdown: '# Script' }, isLoading: false, error: null })),
  useMarkBriefingListened: vi.fn(),
}))
vi.mock('../components/NarrationView', () => ({
  default: ({ linkIndexFallback }: { linkIndexFallback: React.ReactNode }) => <div>NARRATION{linkIndexFallback}</div>,
}))

import { useBriefing, useBriefingEpisodes, useBriefingScript, useMarkBriefingListened } from '../hooks/useApi'
const mockUseBriefing = useBriefing as ReturnType<typeof vi.fn>
const mockUseEpisodes = useBriefingEpisodes as ReturnType<typeof vi.fn>
const mockUseScript = useBriefingScript as ReturnType<typeof vi.fn>
const mockMarkListened = useMarkBriefingListened as ReturnType<typeof vi.fn>

const PODCASTS = [
  {
    id: 'pod-a',
    title: 'Show A',
    slug: 'show-a',
    image_url: 'https://img/a.jpg',
    episodes: [
      {
        id: 'ep-1',
        title: 'First episode',
        slug: 'first',
        pub_date: '2026-09-06T07:00:00Z',
        duration: 1800,
        duration_formatted: '30:00',
        image_url: null,
        summary_available: true,
        summary_preview: 'The gist.',
      },
    ],
  },
  { id: 'pod-b', title: 'Show B', slug: 'show-b', image_url: 'https://img/b.jpg', episodes: [] },
]

function episodesResult(podcasts: unknown[]) {
  return { data: { podcasts }, isLoading: false, isSuccess: true, isError: false }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/briefings/b1']}>
      <Routes>
        <Route path="/briefings/:briefingId" element={<BriefingDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BriefingDetail header (spec #76 phase 3)', () => {
  const mutate = vi.fn()
  beforeEach(() => {
    vi.clearAllMocks()
    mockMarkListened.mockReturnValue({ mutate, isPending: false })
    mockUseEpisodes.mockReturnValue(episodesResult(PODCASTS))
    mockUseBriefing.mockReturnValue({
      data: { id: 'b1', episode_count: 3, created_at: '2026-09-06T07:00:00Z', listened_at: null, narrations: [] },
      isLoading: false,
      error: null,
    })
  })

  it('renders the eyebrow, title and the Mark listened primary in the action row', async () => {
    renderPage()
    expect(screen.getByRole('heading', { name: "Today's briefing" })).toBeInTheDocument()
    const eyebrow = screen.getByText('3 episodes').closest('p')!
    expect(eyebrow).toHaveTextContent(/Generated/)
    expect(eyebrow).not.toHaveTextContent(/Listened/)
    const button = screen.getByRole('button', { name: 'Mark listened' })
    expect(button.className).toContain('rounded-full')
    await userEvent.setup().click(button)
    expect(mutate).toHaveBeenCalledWith('b1')
    expect(screen.getByText('NARRATION')).toBeInTheDocument()
  })

  it('disables the action and notes the listen time once listened', () => {
    mockUseBriefing.mockReturnValue({
      data: { id: 'b1', episode_count: 1, created_at: '2026-09-06T07:00:00Z', listened_at: '2026-09-06T09:00:00Z', narrations: [] },
      isLoading: false,
      error: null,
    })
    renderPage()
    // The count also heads the episode index, so pin the eyebrow's copy.
    expect(screen.getByText('1 episode', { selector: '.text-eyebrow span' }).closest('p')).toHaveTextContent(/Listened/)
    expect(screen.getByRole('button', { name: 'Marked listened' })).toBeDisabled()
  })
})

describe('BriefingDetail artwork index', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockMarkListened.mockReturnValue({ mutate: vi.fn(), isPending: false })
    mockUseBriefing.mockReturnValue({
      data: { id: 'b1', episode_count: 1, created_at: '2026-09-06T07:00:00Z', listened_at: null, narrations: [] },
      isLoading: false,
      error: null,
    })
  })

  it('puts the covered shows in the hero and renders the episode cards instead of the script', () => {
    mockUseEpisodes.mockReturnValue(episodesResult(PODCASTS))
    renderPage()
    expect(screen.getByRole('img', { name: 'Artwork from Show A, Show B' })).toBeInTheDocument()
    expect(screen.getByText('From Show A and Show B')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'First episode' })).toHaveAttribute(
      'href',
      '/podcasts/show-a/episodes/first',
    )
    expect(screen.getByText('The gist.')).toBeInTheDocument()
    expect(screen.queryByText('Script')).not.toBeInTheDocument()
    // The script is not fetched while the index renders.
    expect(mockUseScript).toHaveBeenLastCalledWith(null)
  })

  it('shows a cover placeholder and card skeleton while the index loads', () => {
    mockUseEpisodes.mockReturnValue({ data: undefined, isLoading: true, isSuccess: false, isError: false })
    const { container } = renderPage()
    expect(container.querySelector('.animate-pulse.w-28')).not.toBeNull()
    expect(screen.queryByText('Script')).not.toBeInTheDocument()
    expect(mockUseScript).toHaveBeenLastCalledWith(null)
  })

  it('falls back to the script when the index is empty or fails', () => {
    mockUseEpisodes.mockReturnValue(episodesResult([]))
    const { unmount } = renderPage()
    expect(screen.getByText('Script')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(mockUseScript).toHaveBeenLastCalledWith('b1')
    unmount()

    mockUseEpisodes.mockReturnValue({ data: undefined, isLoading: false, isSuccess: false, isError: true })
    renderPage()
    expect(screen.getByText('Script')).toBeInTheDocument()
    expect(mockUseScript).toHaveBeenLastCalledWith('b1')
  })
})

