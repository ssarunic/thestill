import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import EpisodeReader from './EpisodeReader'
import EpisodeDetail from '../pages/EpisodeDetail'
import { PlayerProvider } from '../contexts/PlayerContext'
import { ToastProvider } from './Toast'
import type { EpisodeDetailResponse } from '../api/types'

// Spec #52 — the reader must render the same content in page mode
// (EpisodeDetail = breadcrumb + reader) and overlay mode (chrome + reader).
// Data hooks are mocked; child components that fetch on their own are
// stubbed so this stays a reader-shape test.

vi.mock('../hooks/useApi', () => ({
  useEpisode: vi.fn(),
  useEpisodeTranscript: vi.fn(() => ({ data: undefined, isLoading: false })),
  useEpisodeSummary: vi.fn(),
  useEpisodeTranscriptWords: vi.fn(() => ({ data: undefined, isFetched: false })),
  useEpisodeEntities: vi.fn(() => ({ data: { entities: [] } })),
  useRelatedEpisodes: vi.fn(() => ({ data: { episodes: [] }, isLoading: false })),
  useMarkInboxReadOnView: vi.fn(),
  useEpisodeLiveRefresh: vi.fn(() => ({ settled: false, contentTerminal: false })),
  useEpisodeTasks: vi.fn(() => ({ data: { tasks: [] } })),
}))

vi.mock('./EntityBranchProgress', () => ({ default: () => null }))
vi.mock('./SummaryViewer', () => ({ default: () => <div>SUMMARY_VIEWER</div> }))

import {
  useEpisode,
  useEpisodeSummary,
  useMarkInboxReadOnView,
  useEpisodeLiveRefresh,
} from '../hooks/useApi'

const mockUseEpisode = useEpisode as ReturnType<typeof vi.fn>
const mockUseSummary = useEpisodeSummary as ReturnType<typeof vi.fn>
const mockMarkOnView = useMarkInboxReadOnView as ReturnType<typeof vi.fn>
const mockLiveRefresh = useEpisodeLiveRefresh as ReturnType<typeof vi.fn>

function episodeResponse(): EpisodeDetailResponse {
  return {
    status: 'ok',
    timestamp: '2026-07-08T00:00:00Z',
    episode: {
      id: 'ep-1',
      podcast_id: 'p-1',
      podcast_slug: 'sample-pod',
      podcast_title: 'Sample Pod',
      title: 'Sample Episode',
      description: 'A test episode',
      slug: 'sample-episode',
      pub_date: '2026-07-01T00:00:00Z',
      audio_url: 'https://example.com/x.mp3',
      duration: 3600,
      duration_formatted: '1:00:00',
      external_id: 'ext-1',
      state: 'summarized',
      has_transcript: true,
      has_summary: true,
      image_url: null,
      podcast_image_url: null,
    },
  }
}

function renderAtEpisodeRoute(element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <PlayerProvider>
          <MemoryRouter initialEntries={['/podcasts/sample-pod/episodes/sample-episode']}>
            <Routes>
              <Route path="/podcasts/:podcastSlug/episodes/:episodeSlug" element={element} />
            </Routes>
          </MemoryRouter>
        </PlayerProvider>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

async function expectReaderContent() {
  expect(screen.getByRole('heading', { name: 'Sample Episode' })).toBeInTheDocument()
  // Podcast title links to the podcast page in both modes (spec #52
  // interaction table — plain navigation, leaves any overlay context).
  // Page mode has a second match: the breadcrumb's podcast link.
  const podcastLinks = screen.getAllByRole('link', { name: 'Sample Pod' })
  expect(podcastLinks.length).toBeGreaterThanOrEqual(1)
  for (const link of podcastLinks) {
    expect(link).toHaveAttribute('href', '/podcasts/sample-pod')
  }
  // Spec #76 §5.5 — pipeline state is operator information; the reader
  // never shows a state pill above the primary action.
  expect(screen.queryByText('Ready')).toBeNull()
  // Spec #76 §3.2 — the primary action carries the duration.
  expect(screen.getByRole('button', { name: 'Play episode, 60 min' })).toHaveTextContent('60 min')
  // jsdom has no Web Share API, so the share action names its fallback.
  expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /summary/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /transcript/i })).toBeInTheDocument()
  // Lazy viewer resolves async
  expect(await screen.findByText('SUMMARY_VIEWER')).toBeInTheDocument()
}

describe('EpisodeReader page/overlay parity (spec #52)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseSummary.mockReturnValue({
      data: { status: 'ok', timestamp: '', available: true, content: '# Summary' },
      isLoading: false,
      isFetching: false,
    })
    mockUseEpisode.mockReturnValue({
      data: episodeResponse(),
      isLoading: false,
      error: null,
    })
  })

  it('renders the reader content standalone (overlay mode)', async () => {
    renderAtEpisodeRoute(<EpisodeReader />)
    await expectReaderContent()
    // No page breadcrumb in overlay mode
    expect(screen.queryByRole('link', { name: 'Podcasts' })).toBeNull()
  })

  it('renders identical reader content inside EpisodeDetail (page mode), plus the breadcrumb', async () => {
    renderAtEpisodeRoute(<EpisodeDetail />)
    await expectReaderContent()
    expect(screen.getByRole('link', { name: 'Podcasts' })).toBeInTheDocument()
  })

  it('inherits read-on-view marking (spec #29) in both modes', () => {
    renderAtEpisodeRoute(<EpisodeReader />)
    expect(mockMarkOnView).toHaveBeenCalledWith('ep-1', true)

    mockMarkOnView.mockClear()
    renderAtEpisodeRoute(<EpisodeDetail />)
    expect(mockMarkOnView).toHaveBeenCalledWith('ep-1', true)
  })

  it('inherits live pipeline refresh (spec #68) in both modes', () => {
    // The hook lives on the reader, not on either shell, so the page and
    // the overlay cannot drift apart on freshness (spec #42 FM-6). Both
    // modes must hand it the same episode and the same content-availability
    // signals it reconciles against.
    const expected = {
      podcastSlug: 'sample-pod',
      episodeSlug: 'sample-episode',
      episode: expect.objectContaining({ id: 'ep-1', state: 'summarized' }),
      transcriptAvailable: undefined,
      summaryAvailable: true,
    }

    renderAtEpisodeRoute(<EpisodeReader />)
    expect(mockLiveRefresh).toHaveBeenCalledWith(expect.objectContaining(expected))

    mockLiveRefresh.mockClear()
    renderAtEpisodeRoute(<EpisodeDetail />)
    expect(mockLiveRefresh).toHaveBeenCalledWith(expect.objectContaining(expected))
  })

  it('renders the eyebrow, show row and show-notes action from the episode (spec #76 §3.1–3.2)', () => {
    const response = episodeResponse()
    response.episode.season_number = 3
    response.episode.episode_number = 12
    response.episode.explicit = true
    response.episode.episode_type = 'bonus'
    response.episode.website_url = 'https://example.com/notes'
    mockUseEpisode.mockReturnValue({ data: response, isLoading: false, error: null })

    renderAtEpisodeRoute(<EpisodeReader />)

    const eyebrow = screen.getByText('S3 E12').closest('p')!
    expect(eyebrow).toHaveTextContent('Bonus')
    expect(eyebrow).toHaveTextContent('Explicit')
    expect(screen.getByRole('link', { name: 'Show notes' })).toHaveAttribute('href', 'https://example.com/notes')
    // Watch video only under the spec #62 §6 condition — absent here.
    expect(screen.queryByRole('button', { name: 'Watch video' })).toBeNull()
  })

  it('offers Watch video for an audio episode with a YouTube rendition (spec #62 §6)', () => {
    const response = episodeResponse()
    response.episode.playback = {
      kind: 'audio',
      audio: { url: 'https://example.com/x.mp3', mime_type: 'audio/mpeg' },
      video: null,
      poster_url: null,
      youtube: { video_id: 'abc123', url: 'https://youtube.com/watch?v=abc123' },
    } as never
    mockUseEpisode.mockReturnValue({ data: response, isLoading: false, error: null })

    renderAtEpisodeRoute(<EpisodeReader />)
    expect(screen.getByRole('button', { name: 'Watch video' })).toBeInTheDocument()
  })

  it('shows the error card when the episode fails to load', () => {
    mockUseEpisode.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('boom'),
    })
    renderAtEpisodeRoute(<EpisodeReader />)
    expect(screen.getByText('Error loading episode')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('shows an original/default-language toggle and keys the request from the URL', async () => {
    const languageSpy = vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('en-GB')
    mockUseSummary.mockImplementation((_podcastSlug: string, _episodeSlug: string, lang?: string) => ({
      data: {
        status: 'ok',
        timestamp: '',
        available: true,
        content: lang === 'en' ? '# Summary' : '# Sažetak',
        language: lang ?? 'hr',
        podcast_language: 'hr',
        canonical_language: 'hr',
        available_languages: ['hr', ...(lang === 'en' ? ['en'] : [])],
      },
      isLoading: false,
      isFetching: false,
    }))
    const user = userEvent.setup()

    renderAtEpisodeRoute(<EpisodeReader />)

    expect(await screen.findByRole('button', { name: 'HR (original)' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'EN' }))
    await waitFor(() => {
      expect(mockUseSummary).toHaveBeenLastCalledWith('sample-pod', 'sample-episode', 'en')
    })
    expect(screen.getByRole('button', { name: 'EN' })).toHaveAttribute('aria-pressed', 'true')
    languageSpy.mockRestore()
  })

  it('treats legacy English as canonical and shows progress while creating Croatian', async () => {
    const languageSpy = vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('en-GB')
    mockUseSummary.mockImplementation((_podcastSlug: string, _episodeSlug: string, lang?: string) => ({
      data: {
        status: 'ok',
        timestamp: '',
        available: true,
        content: '# The Gist',
        language: 'en',
        podcast_language: 'hr',
        canonical_language: 'en',
        available_languages: ['en'],
      },
      isLoading: false,
      isFetching: lang === 'hr',
    }))
    const user = userEvent.setup()

    renderAtEpisodeRoute(<EpisodeReader />)

    expect(await screen.findByRole('button', { name: 'EN' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'HR (original)' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Translating to HR…')
    expect(screen.getByRole('button', { name: 'HR (original)' })).toHaveAttribute('aria-pressed', 'true')
    languageSpy.mockRestore()
  })
})
