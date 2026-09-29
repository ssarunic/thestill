// Spec #28 §4.2 — search results page quote rows must play inline
// through the FloatingPlayer, not navigate the user to the episode
// page. Regression test for the bug where every quote click closed the
// results list — even the file's own header comment said the row was
// supposed to play inline, but the click handler called navigate().

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SearchResults from './SearchResults'
import type { SearchResponse, SearchResult, QuickSearchResponse } from '../api/types'

const playMock = vi.fn()
const seekMock = vi.fn()
const resumeMock = vi.fn()
let isCurrentTrack = false
let isPlayingNow = false
const navigateMock = vi.fn()

vi.mock('../contexts/PlayerContext', () => ({
  usePlayer: () => ({
    track: null,
    isPlaying: isPlayingNow,
    isLoading: false,
    duration: 0,
    playbackRate: 1,
    play: playMock,
    pause: vi.fn(),
    resume: resumeMock,
    toggle: vi.fn(),
    seek: seekMock,
    skip: vi.fn(),
    setRate: vi.fn(),
    stop: vi.fn(),
    isCurrent: () => isCurrentTrack,
    getCurrentTime: () => 0,
  }),
  usePlayerTime: () => 0,
  PlayerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

// react-router-dom's useNavigate — we keep the rest of the module so
// <MemoryRouter> still works, but override the navigate hook so the
// test can assert when the page falls back to navigation.
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

vi.mock('../api/client', () => ({
  corpusSearch: vi.fn(),
  quickSearch: vi.fn(),
}))

import { corpusSearch, quickSearch } from '../api/client'

const mockCorpusSearch = corpusSearch as ReturnType<typeof vi.fn>
const mockQuickSearch = quickSearch as ReturnType<typeof vi.fn>

function row(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    episode_id: 'ep-1',
    podcast_id: 'pod-1',
    podcast_slug: 'twenty-minute-vc',
    episode_slug: 'cliff-weitzman-on-speechify',
    podcast_title: 'The Twenty Minute VC',
    episode_title: 'Cliff Weitzman on Speechify',
    published_at: '2026-05-09T00:00:00Z',
    start_ms: 187_000,
    end_ms: 192_000,
    speaker: 'Harry Stebbings',
    quote: 'Cliff is the founder of Speechify.',
    score: 0.9,
    match_type: 'lexical',
    deeplink: '/episodes/ep-1?t=187',
    web_url: '/episodes/ep-1?t=187',
    audio_url: 'https://cdn.example.com/ep-1.mp3',
    image_url: 'https://cdn.example.com/ep-1.jpg',
    duration: 3600,
    ...overrides,
  }
}

function corpusResponse(rows: SearchResult[]): SearchResponse {
  return { query: 'speechify', mode: 'hybrid', total: rows.length, results: rows }
}

function emptyQuick(): QuickSearchResponse {
  return { query: 'speechify', took_ms: 1, groups: [], see_all_url: '/search?q=speechify' }
}

function renderPage(query = 'speechify') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/search?q=${encodeURIComponent(query)}`]}>
        <SearchResults />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SearchResults — quote rows play inline (spec #28 §4.2)', () => {
  beforeEach(() => {
    playMock.mockReset()
    seekMock.mockReset()
    resumeMock.mockReset()
    navigateMock.mockReset()
    mockCorpusSearch.mockReset()
    mockQuickSearch.mockReset()
    isCurrentTrack = false
    isPlayingNow = false
    // Quick search runs in parallel with corpus search but only feeds
    // the Entities tab. Default to an empty response so it doesn't
    // interfere with the All/Quotes tab assertions.
    mockQuickSearch.mockResolvedValue(emptyQuick())
  })

  it('plays the matching quote through the FloatingPlayer on click', async () => {
    mockCorpusSearch.mockResolvedValue(corpusResponse([row()]))
    renderPage()

    const quoteRow = await screen.findByTestId('search-quote-row')
    fireEvent.click(quoteRow)

    // play() should fire with the full track payload — without
    // audio_url plumbed through, the click would have fallen back
    // to navigate() and the player would not have started.
    expect(playMock).toHaveBeenCalledTimes(1)
    expect(playMock).toHaveBeenCalledWith(
      expect.objectContaining({
        episodeId: 'ep-1',
        audioUrl: 'https://cdn.example.com/ep-1.mp3',
        title: 'Cliff Weitzman on Speechify',
        podcastTitle: 'The Twenty Minute VC',
        artworkUrl: 'https://cdn.example.com/ep-1.jpg',
        durationHint: 3600,
      }),
      { startAt: 187 },
    )
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('seeks the existing track when the quote belongs to the currently playing episode', async () => {
    mockCorpusSearch.mockResolvedValue(corpusResponse([row()]))
    isCurrentTrack = true
    isPlayingNow = false
    renderPage()

    const quoteRow = await screen.findByTestId('search-quote-row')
    fireEvent.click(quoteRow)

    expect(playMock).not.toHaveBeenCalled()
    expect(seekMock).toHaveBeenCalledWith(187)
    expect(resumeMock).toHaveBeenCalled()
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('falls back to a navigation when audio_url is missing', async () => {
    mockCorpusSearch.mockResolvedValue(
      corpusResponse([row({ audio_url: null })]),
    )
    renderPage()

    const quoteRow = await screen.findByTestId('search-quote-row')
    fireEvent.click(quoteRow)

    expect(playMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith(
        '/podcasts/twenty-minute-vc/episodes/cliff-weitzman-on-speechify?t=187',
      ),
    )
  })
})

describe('SearchResults — typing is debounced (spec #89)', () => {
  beforeEach(() => {
    mockCorpusSearch.mockReset()
    mockQuickSearch.mockReset()
    mockCorpusSearch.mockResolvedValue(corpusResponse([]))
    mockQuickSearch.mockResolvedValue(emptyQuick())
  })

  it('runs one corpus search for a typed word, not one per keystroke', async () => {
    renderPage('')
    const input = screen.getByPlaceholderText('Search the corpus…')
    let typed = ''
    for (const ch of 'legora') {
      typed += ch
      fireEvent.change(input, { target: { value: typed } })
    }
    await waitFor(() => expect(mockCorpusSearch).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 400))
    const queries = mockCorpusSearch.mock.calls.map((c) => c[0])
    expect(queries).toEqual(['legora'])
  })
})

describe('SearchResults — quotes are grouped by episode', () => {
  beforeEach(() => {
    mockCorpusSearch.mockReset()
    mockQuickSearch.mockReset()
    mockQuickSearch.mockResolvedValue(emptyQuick())
  })

  const flood = [
    row({ start_ms: 60_000, quote: 'Harry Stebbings: first Legora moment' }),
    row({ start_ms: 10_000, quote: 'Harry Stebbings: second Legora moment' }),
  ]
  const other = row({
    episode_id: 'ep-2',
    episode_slug: 'other-episode',
    episode_title: 'Other Episode',
    start_ms: 5_000,
    speaker: null,
    quote: 'Legora came up once here',
  })

  it('asks for a per-episode cap and renders one card per episode with its mention count', async () => {
    mockCorpusSearch.mockResolvedValue({
      ...corpusResponse([flood[0], other, flood[1]]),
      match_counts: { 'ep-1': 27, 'ep-2': 1 },
    })
    renderPage('legora')

    const cards = await screen.findAllByTestId('search-episode-group')
    expect(cards).toHaveLength(2)
    expect(mockCorpusSearch.mock.calls[0][1]).toMatchObject({ per_episode: 2, limit: 50 })
    // Episodes keep the order of their best hit; each card holds its own moments.
    expect(cards[0]).toHaveTextContent('Cliff Weitzman on Speechify')
    expect(cards[0]).toHaveTextContent('27 mentions')
    expect(cards[0].querySelectorAll('[data-testid="search-quote-row"]')).toHaveLength(2)
    expect(cards[1]).toHaveTextContent('Other Episode')
    expect(cards[1]).toHaveTextContent('1 mention')
    // The speaker prefix the index stores isn't repeated inside the quote.
    expect(cards[0]).toHaveTextContent('Harry Stebbings: "first Legora moment"')
    expect(screen.getByText('2 episodes')).toBeInTheDocument()
    // Nothing more to show for an episode whose moments are all on the card.
    expect(cards[1].querySelector('[data-testid="search-episode-expand"]')).toBeNull()
  })

  it('"Show all" loads the episode\'s literal matches in episode order', async () => {
    mockCorpusSearch.mockImplementation(async (_q: string, opts: { episode_id?: string }) =>
      opts.episode_id
        ? corpusResponse([
            row({ start_ms: 90_000, quote: 'late mention' }),
            row({ start_ms: 1_000, quote: 'early mention' }),
            row({ start_ms: 45_000, quote: 'middle mention' }),
          ])
        : { ...corpusResponse(flood), match_counts: { 'ep-1': 3 } },
    )
    renderPage('legora')

    fireEvent.click(await screen.findByText('Show all 3 mentions'))

    await waitFor(() => expect(screen.getByText(/early mention/)).toBeInTheDocument())
    const expandCall = mockCorpusSearch.mock.calls.find((c) => c[1].episode_id)
    expect(expandCall?.[1]).toMatchObject({ episode_id: 'ep-1', mode: 'lexical' })
    const quotes = screen.getAllByTestId('search-quote-row').map((el) => el.textContent)
    expect(quotes.map((q) => q?.match(/(early|middle|late) mention/)?.[1])).toEqual(['early', 'middle', 'late'])
    expect(screen.getByText('Show fewer')).toBeInTheDocument()
  })
})
