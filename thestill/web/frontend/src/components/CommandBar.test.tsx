// Spec #28 §4.1 — the ⌘K command bar's quote hits must play inline
// through the FloatingPlayer, not navigate away to the episode page.
// Regression test for the bug where selecting a quote from typeahead
// closed the bar and threw the user onto /podcasts/.../episodes/...?t=
// instead of seeking the global player.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import CommandBar from './CommandBar'
import type { QuickQuoteItem, QuickSearchResponse } from '../api/types'

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

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

vi.mock('../api/client', () => ({
  quickSearch: vi.fn(),
}))

import { quickSearch } from '../api/client'

const mockQuickSearch = quickSearch as ReturnType<typeof vi.fn>

function quoteItem(overrides: Partial<QuickQuoteItem> = {}): QuickQuoteItem {
  return {
    kind: 'quote',
    episode_id: 'ep-1',
    podcast_id: 'pod-1',
    podcast_slug: 'twenty-minute-vc',
    episode_slug: 'cliff-weitzman-on-speechify',
    podcast_title: 'The Twenty Minute VC',
    episode_title: 'Cliff Weitzman on Speechify',
    speaker: 'Harry Stebbings',
    quote: 'Cliff is the founder of Speechify.',
    start_ms: 187_000,
    end_ms: 192_000,
    score: 0.9,
    audio_url: 'https://cdn.example.com/ep-1.mp3',
    image_url: 'https://cdn.example.com/ep-1.jpg',
    duration: 3600,
    ...overrides,
  }
}

function response(quote: QuickQuoteItem): QuickSearchResponse {
  return {
    query: 'speechify',
    took_ms: 1,
    groups: [
      { type: 'episode', label: 'Episodes', items: [] },
      { type: 'person', label: 'People', items: [] },
      { type: 'company', label: 'Companies', items: [] },
      { type: 'topic', label: 'Topics', items: [] },
      { type: 'quote', label: 'Quotes', items: [quote] },
    ],
    see_all_url: '/search?q=speechify',
  }
}

function renderBar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CommandBar isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function typeAndSelect(value: string) {
  const input = await screen.findByLabelText('Search query')
  fireEvent.change(input, { target: { value } })
  const row = await screen.findByTestId('cmdk-item-quote')
  fireEvent.click(row)
}

describe('CommandBar — quote selection plays inline (spec #28 §4.1)', () => {
  beforeEach(() => {
    playMock.mockReset()
    seekMock.mockReset()
    resumeMock.mockReset()
    navigateMock.mockReset()
    mockQuickSearch.mockReset()
    isCurrentTrack = false
    isPlayingNow = false
  })

  it('seeks the FloatingPlayer when a quote hit is selected', async () => {
    mockQuickSearch.mockResolvedValue(response(quoteItem()))
    renderBar()

    await typeAndSelect('speechify')

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

  it('falls back to a navigation when the quote has no audio_url', async () => {
    mockQuickSearch.mockResolvedValue(response(quoteItem({ audio_url: null })))
    renderBar()

    await typeAndSelect('speechify')

    expect(playMock).not.toHaveBeenCalled()
    expect(navigateMock).toHaveBeenCalledWith(
      '/podcasts/twenty-minute-vc/episodes/cliff-weitzman-on-speechify?t=187',
    )
  })

  it('seeks the existing track when the quote belongs to the currently playing episode', async () => {
    mockQuickSearch.mockResolvedValue(response(quoteItem()))
    isCurrentTrack = true
    isPlayingNow = false
    renderBar()

    await typeAndSelect('speechify')

    expect(playMock).not.toHaveBeenCalled()
    expect(seekMock).toHaveBeenCalledWith(187)
    expect(resumeMock).toHaveBeenCalled()
    expect(navigateMock).not.toHaveBeenCalled()
  })
})
