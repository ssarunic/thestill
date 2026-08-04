import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEpisode, useEpisodeSummary, useEpisodeLiveRefresh } from './useApi'
import type { EpisodeDetailResponse } from '../api/types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, getEpisode: vi.fn(), getEpisodeSummary: vi.fn() }
})

import { getEpisode, getEpisodeSummary } from '../api/client'

const mockGetEpisode = getEpisode as ReturnType<typeof vi.fn>
const mockGetEpisodeSummary = getEpisodeSummary as ReturnType<typeof vi.fn>

/**
 * Spec #68 — end-to-end reader freshness, with nothing stubbed between the
 * episode poll and the rendered summary.
 *
 * The unit tests assert that `invalidateQueries` is *called*; that is not the
 * same claim as "the summary reaches the screen". They would still pass if the
 * episode query stopped polling, or if an invalidation targeted a key that
 * refetched nothing — which is precisely the failure this spec exists to fix
 * (spec #42 FM-5: a test can pass because its mocks agree with each other).
 *
 * So this file uses a real QueryClient with the app-wide defaults from
 * main.tsx, mocks only the HTTP layer, and asserts on rendered text.
 */

const PODCAST = 'the-show'
const EPISODE = 'ep-1'
const SUMMARY_TEXT = '# What they discussed'
const PENDING_TEXT = 'Summary not yet available'

function episodeResponse(overrides: {
  state: string
  has_summary: boolean
}): EpisodeDetailResponse {
  return {
    status: 'ok',
    timestamp: '2026-08-03T00:00:00Z',
    episode: {
      id: 'ep-uuid-1',
      state: overrides.state,
      has_transcript: true,
      has_summary: overrides.has_summary,
    },
  } as EpisodeDetailResponse
}

/** Minimal stand-in for the reader: same three hooks, same wiring. */
function Reader() {
  const { data: episodeData } = useEpisode(PODCAST, EPISODE)
  const { data: summaryData } = useEpisodeSummary(PODCAST, EPISODE)
  useEpisodeLiveRefresh({
    podcastSlug: PODCAST,
    episodeSlug: EPISODE,
    episode: episodeData?.episode,
    transcriptAvailable: undefined,
    summaryAvailable: summaryData?.available,
  })
  return (
    <div>
      <span data-testid="state">{episodeData?.episode?.state ?? '—'}</span>
      <span data-testid="summary">
        {summaryData?.available ? summaryData.content : PENDING_TEXT}
      </span>
    </div>
  )
}

describe('reader freshness (integration)', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.useFakeTimers()
    // The app-wide defaults from main.tsx — the 5s clock this design rides on.
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 5_000, refetchInterval: 5_000, retry: false },
      },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  async function tick(ms: number) {
    // Flush pending microtasks/zero-delay timers first, then step. Stepping
    // matters: effects re-arm work between ticks, and one big jump would drain
    // the timer queue before React ever re-rendered.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    for (let elapsed = 0; elapsed < ms; elapsed += 1_000) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(Math.min(1_000, ms - elapsed))
      })
    }
  }

  it('shows the summary once the pipeline produces it, with no reload and no completion edge', async () => {
    // Mid-pipeline: cleaned, no summary yet.
    mockGetEpisode.mockResolvedValue(episodeResponse({ state: 'cleaned', has_summary: false }))
    mockGetEpisodeSummary.mockResolvedValue({ available: false, content: '' })

    render(
      <QueryClientProvider client={queryClient}>
        <Reader />
      </QueryClientProvider>,
    )

    await tick(0)
    expect(screen.getByTestId('state')).toHaveTextContent('cleaned')
    expect(screen.getByTestId('summary')).toHaveTextContent(PENDING_TEXT)

    // Summarization completes server-side. No task-completion edge is
    // delivered to the client at any point — the task list stays empty, which
    // is exactly the non-atomic window that used to wedge the old bridge.
    mockGetEpisodeSummary.mockResolvedValue({ available: true, content: SUMMARY_TEXT })
    mockGetEpisode.mockResolvedValue(episodeResponse({ state: 'summarized', has_summary: true }))

    // One episode poll observes the new level; the invalidation it triggers
    // must actually refetch the summary and put it on screen.
    await tick(15_000)

    expect(screen.getByTestId('state')).toHaveTextContent('summarized')
    expect(screen.getByTestId('summary')).toHaveTextContent('What they discussed')
  })

  it('recovers a summary that was already present when the reader mounted', async () => {
    // Back-navigation into a cached `available: false` while the episode
    // payload already says the summary exists — no transition to observe, so
    // only the mount reconciliation can fix this.
    mockGetEpisode.mockResolvedValue(episodeResponse({ state: 'summarized', has_summary: true }))
    mockGetEpisodeSummary.mockResolvedValueOnce({ available: false, content: '' })
    mockGetEpisodeSummary.mockResolvedValue({ available: true, content: SUMMARY_TEXT })

    render(
      <QueryClientProvider client={queryClient}>
        <Reader />
      </QueryClientProvider>,
    )

    await tick(0)
    expect(screen.getByTestId('summary')).toHaveTextContent(PENDING_TEXT)

    await tick(5_000)
    expect(screen.getByTestId('summary')).toHaveTextContent('What they discussed')
  })

  it('stops polling the episode once it is settled', async () => {
    mockGetEpisode.mockResolvedValue(episodeResponse({ state: 'summarized', has_summary: true }))
    mockGetEpisodeSummary.mockResolvedValue({ available: true, content: SUMMARY_TEXT })

    function SettlingReader() {
      const live = useEpisodeLiveRefresh({
        podcastSlug: PODCAST,
        episodeSlug: EPISODE,
        episode: useEpisode(PODCAST, EPISODE, { live: false }).data?.episode,
        // Both artifacts have to have reported before the episode counts as
        // settled — "terminal state" alone would stop the clock while content
        // was still in flight.
        transcriptAvailable: true,
        summaryAvailable: useEpisodeSummary(PODCAST, EPISODE).data?.available,
      })
      return <span data-testid="settled">{String(live.settled)}</span>
    }

    render(
      <QueryClientProvider client={queryClient}>
        <SettlingReader />
      </QueryClientProvider>,
    )

    await tick(5_000)
    expect(screen.getByTestId('settled')).toHaveTextContent('true')

    // A reader parked on a finished episode must go quiet — otherwise an open
    // tab costs ~17k requests/day.
    const settledCallCount = mockGetEpisode.mock.calls.length
    await tick(60_000)
    expect(mockGetEpisode).toHaveBeenCalledTimes(settledCallCount)
  })
})
