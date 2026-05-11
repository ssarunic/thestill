// Spec #28 §5.1 — the entity page's "Recent mentions" timestamps must
// open the FloatingPlayer inline at the right moment instead of
// navigating the user away to the episode detail page (which loses
// their place on the entity page). Regression test for the bug where
// the floating player "doesn't work" on /entities/:type/:slug — the
// only affordance was a deep-link to the episode page, so the player
// never showed up while the user was browsing entity mentions.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Entities from './Entities'
import type { EntitySummaryResponse } from '../api/types'

// Player context — capture every play() call so we can assert that
// clicking a timestamp seeks the right track at the right offset.
const playMock = vi.fn()
const seekMock = vi.fn()
const resumeMock = vi.fn()
let isCurrentTrack = false
let isPlayingNow = false

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

vi.mock('../api/client', () => ({
  getEntitySummary: vi.fn(),
}))

import { getEntitySummary } from '../api/client'

const mockGetEntitySummary = getEntitySummary as ReturnType<typeof vi.fn>

function summary(overrides: Partial<EntitySummaryResponse> = {}): EntitySummaryResponse {
  return {
    entity: {
      id: 'person:cliff-weitzman',
      type: 'person',
      canonical_name: 'Cliff Weitzman',
      wikidata_qid: null,
    },
    aliases: [],
    description: null,
    mention_count: 1,
    cooccurring: [],
    recent_mentions: [
      {
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
        surface_form: 'Cliff Weitzman',
        audio_url: 'https://cdn.example.com/ep-1.mp3',
        image_url: 'https://cdn.example.com/ep-1.jpg',
        duration: 3600,
      },
    ],
    hosts_podcasts: [],
    recurring_podcasts: [],
    guest_episodes: [],
    ...overrides,
  }
}

function renderPage(initialPath = '/entities/person/cliff-weitzman') {
  // ``staleTime: Infinity`` keeps the mock from being re-invoked
  // between renders within the same test.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/entities/:entityType/:idSlug" element={<Entities />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Entities page — FloatingPlayer wiring (spec #28 §5.1)', () => {
  beforeEach(() => {
    playMock.mockReset()
    seekMock.mockReset()
    resumeMock.mockReset()
    mockGetEntitySummary.mockReset()
    isCurrentTrack = false
    isPlayingNow = false
  })

  it('renders the recent-mention timestamp as a play button when audio_url is present', async () => {
    mockGetEntitySummary.mockResolvedValue(summary())
    renderPage()

    // Page should land on a button (not a Link) for the timestamp. The
    // bug was that this was always a <Link to=".../episodes/...?t=N">
    // which navigated away from the entity page.
    const playBtn = await screen.findByRole('button', {
      name: /Play "Cliff is the founder of Speechify\." at 3:07/,
    })
    expect(playBtn).toBeInTheDocument()
    expect(playBtn.tagName).toBe('BUTTON')
  })

  it('hands the audio URL and start offset to the FloatingPlayer on click', async () => {
    mockGetEntitySummary.mockResolvedValue(summary())
    renderPage()

    const playBtn = await screen.findByRole('button', { name: /Play .* at 3:07/ })
    await userEvent.click(playBtn)

    // play() must receive the full PlayerTrack shape — the bug
    // would have surfaced here as a click that does nothing because
    // the page only knew the episode slug, not the audio URL.
    expect(playMock).toHaveBeenCalledTimes(1)
    expect(playMock).toHaveBeenCalledWith(
      expect.objectContaining({
        episodeId: 'ep-1',
        podcastSlug: 'twenty-minute-vc',
        episodeSlug: 'cliff-weitzman-on-speechify',
        audioUrl: 'https://cdn.example.com/ep-1.mp3',
        title: 'Cliff Weitzman on Speechify',
        podcastTitle: 'The Twenty Minute VC',
        artworkUrl: 'https://cdn.example.com/ep-1.jpg',
        durationHint: 3600,
      }),
      { startAt: 187 },
    )
  })

  it('seeks the existing track when the mention belongs to the currently-playing episode', async () => {
    mockGetEntitySummary.mockResolvedValue(summary())
    isCurrentTrack = true
    isPlayingNow = false
    renderPage()

    const playBtn = await screen.findByRole('button', { name: /Play .* at 3:07/ })
    await userEvent.click(playBtn)

    // No play() — we don't want to reload the same audio source. Just
    // seek and resume (since playback was paused).
    expect(playMock).not.toHaveBeenCalled()
    expect(seekMock).toHaveBeenCalledWith(187)
    expect(resumeMock).toHaveBeenCalled()
  })

  it('falls back to a deep-link when the API response omits audio_url', async () => {
    // Older API responses (and episodes whose feed never published an
    // audio URL) should keep the old navigation behavior — no audio
    // means we can't play inline.
    const body = summary()
    body.recent_mentions[0].audio_url = null
    mockGetEntitySummary.mockResolvedValue(body)
    renderPage()

    // Wait for either button or link variant to appear, then assert
    // it's the link variant.
    await waitFor(() =>
      expect(screen.queryByText('3:07')).toBeInTheDocument(),
    )
    expect(
      screen.queryByRole('button', { name: /Play .* at 3:07/ }),
    ).toBeNull()
    const link = screen.getByRole('link', { name: '3:07' })
    expect(link).toHaveAttribute(
      'href',
      '/podcasts/twenty-minute-vc/episodes/cliff-weitzman-on-speechify?t=187',
    )
  })
})
