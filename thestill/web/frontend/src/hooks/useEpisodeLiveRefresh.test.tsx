import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEpisodeLiveRefresh } from './useApi'
import type { EpisodeDetail } from '../api/types'

/**
 * Spec #68 D3 — the reader's frozen transcript/summary queries reconciled
 * against the live episode query.
 *
 * Per spec #42 FM-5, these fixtures deliberately script the *awkward*
 * orderings: flags observed as already-true on mount, flags travelling
 * true→false→true across a re-transcription, and the permanent
 * `has_summary && !available` state an N/A summary produces. A fixture
 * where every transition is politely observed would pass against the
 * edge-triggered implementation this hook replaces.
 */

const PODCAST = 'the-show'
const EPISODE = 'ep-1'

const TRANSCRIPT_KEY = ['episodes', PODCAST, EPISODE, 'transcript']
const SUMMARY_KEY = ['episodes', PODCAST, EPISODE, 'summary']

/** Mirrors RECONCILE_MAX_ATTEMPTS in useApi.ts. */
const RECONCILE_MAX_ATTEMPTS = 3

function episodeAt(overrides: Partial<EpisodeDetail> = {}): EpisodeDetail {
  return {
    id: 'ep-uuid-1',
    state: 'cleaned',
    has_transcript: true,
    has_summary: false,
    ...overrides,
  } as EpisodeDetail
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function renderLiveRefresh(
  queryClient: QueryClient,
  initialProps: {
    episode: EpisodeDetail | undefined
    transcriptAvailable?: boolean | undefined
    summaryAvailable?: boolean | undefined
  },
) {
  return renderHook(
    (props: {
      episode: EpisodeDetail | undefined
      transcriptAvailable?: boolean | undefined
      summaryAvailable?: boolean | undefined
    }) =>
      useEpisodeLiveRefresh({
        podcastSlug: PODCAST,
        episodeSlug: EPISODE,
        episode: props.episode,
        transcriptAvailable: props.transcriptAvailable,
        summaryAvailable: props.summaryAvailable,
      }),
    { wrapper: createWrapper(queryClient), initialProps },
  )
}

/** Keys passed to invalidateQueries, flattened to a comparable string. */
function invalidatedKeys(spy: ReturnType<typeof vi.fn>): string[] {
  return spy.mock.calls.map(([arg]) => JSON.stringify(arg.queryKey))
}

function countKey(spy: ReturnType<typeof vi.fn>, key: unknown[]): number {
  return invalidatedKeys(spy).filter((k) => k === JSON.stringify(key)).length
}

describe('useEpisodeLiveRefresh', () => {
  let queryClient: QueryClient
  let invalidateSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    invalidateSpy = vi.fn()
    queryClient.invalidateQueries = invalidateSpy as never
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  /**
   * Reconciliation attempts are scheduled, not synchronous — an attempt only
   * counts once it has actually run, so a failed refetch cannot silently
   * consume the budget. Advance far enough to flush the whole backoff chain
   * (0ms, +3s, +9s) unless a test wants to observe it mid-flight.
   */
  async function flushReconcile(totalMs = 0) {
    // Stepped, not one big jump: each attempt re-arms the next one from a
    // React effect, so the render pass has to run between timers. A single
    // `advanceTimersByTimeAsync(everything)` drains the queue before any
    // re-render and would observe only the first attempt.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    for (let elapsed = 0; elapsed < totalMs; elapsed += 1_000) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(Math.min(1_000, totalMs - elapsed))
      })
    }
  }

  describe('(a) tuple-diff invalidation', () => {
    it('does not invalidate on the first observation of an episode', () => {
      renderLiveRefresh(queryClient, { episode: episodeAt() })
      expect(invalidateSpy).not.toHaveBeenCalled()
    })

    it('invalidates the summary prefix when has_summary flips true', () => {
      const { rerender } = renderLiveRefresh(queryClient, { episode: episodeAt() })

      rerender({ episode: episodeAt({ state: 'summarized', has_summary: true }) })

      // Prefix key, so every cached language variant refetches — the full
      // key is [..., 'summary', lang].
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)
    })

    it('invalidates the transcript prefix when has_transcript flips true', () => {
      const { rerender } = renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'transcribed', has_transcript: false }),
      })

      rerender({ episode: episodeAt({ state: 'cleaned', has_transcript: true }) })

      expect(countKey(invalidateSpy, TRANSCRIPT_KEY)).toBe(1)
    })

    it('re-arms the task poll and entity queries on any state change', () => {
      const { rerender } = renderLiveRefresh(queryClient, { episode: episodeAt() })

      rerender({ episode: episodeAt({ state: 'summarized', has_summary: true }) })

      // Work queued elsewhere (CLI, scheduler) is invisible to a stopped
      // task poll; a state change is the signal to start looking again.
      expect(invalidatedKeys(invalidateSpy)).toContain(
        JSON.stringify(['episodes', 'tasks', 'ep-uuid-1']),
      )
      expect(invalidatedKeys(invalidateSpy)).toContain(
        JSON.stringify(['episodes', 'ep-uuid-1', 'entities']),
      )
      expect(invalidatedKeys(invalidateSpy)).toContain(
        JSON.stringify(['episodes', 'ep-uuid-1', 'related']),
      )
    })

    it('invalidates on regeneration, in both directions', () => {
      // Re-transcription clears clean_transcript_path AND summary_path
      // server-side (task_handlers.py), so these flags are not monotonic.
      const { rerender } = renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
      })

      // true → false: the old summary is gone, drop the stale content.
      rerender({ episode: episodeAt({ state: 'transcribed', has_summary: false }) })
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)

      // false → true: the regenerated summary landed.
      rerender({ episode: episodeAt({ state: 'summarized', has_summary: true }) })
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(2)
    })

    it('does not invalidate when an unchanged episode re-renders', () => {
      const { rerender } = renderLiveRefresh(queryClient, { episode: episodeAt() })

      // The 5s poll re-delivers identical data constantly; that must not
      // translate into a refetch storm on the content queries.
      rerender({ episode: episodeAt() })
      rerender({ episode: episodeAt() })

      expect(invalidateSpy).not.toHaveBeenCalled()
    })

    it('does not invalidate when switching to a different episode', () => {
      const { rerender } = renderLiveRefresh(queryClient, { episode: episodeAt() })

      // Navigation, not progress — the new episode's queries were just
      // fetched and need no reconciliation.
      rerender({
        episode: episodeAt({ id: 'ep-uuid-2', state: 'summarized', has_summary: true }),
      })

      expect(invalidateSpy).not.toHaveBeenCalled()
    })
  })

  describe('(b) mount reconciliation with bounded retries', () => {
    it('reconciles a stale available:false when the flag was already true on mount', async () => {
      // Back-navigation into a 60s-fresh cached `available: false` while the
      // episode payload already says the artifact exists. There is no
      // transition to observe, so (a) cannot help here.
      renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      await flushReconcile(0)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)
    })

    it('reconciles the transcript symmetrically', async () => {
      renderLiveRefresh(queryClient, {
        episode: episodeAt({ has_transcript: true }),
        transcriptAvailable: false,
      })

      await flushReconcile(0)
      expect(countKey(invalidateSpy, TRANSCRIPT_KEY)).toBe(1)
    })

    it('retries when the refetch leaves the artifact still unavailable', async () => {
      // The first invalidation is not a guarantee: the refetch can fail, or
      // return the same stale response. Marking the artifact reconciled at
      // request time would spend the only attempt on that failure and leave
      // the reader stale until focus or reload.
      renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      await flushReconcile(0)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)

      await flushReconcile(3_000)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(2)
    })

    it('stops after the attempt budget for a permanently unavailable summary', () => {
      // An N/A summary (spec #41) is a legitimate, permanent
      // `has_summary: true && available: false`. Retries must be capped or
      // they spin on these episodes forever.
      renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      // Well past the whole backoff chain (0ms, +3s, +9s).
      return flushReconcile(30_000).then(() => {
        expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(RECONCILE_MAX_ATTEMPTS)
      })
    })

    it('stops retrying as soon as the content arrives', async () => {
      const { rerender } = renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      await flushReconcile(0)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)

      rerender({
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: true,
      })

      await flushReconcile(30_000)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)
    })

    it('does not reconcile while the content query is still loading', () => {
      // `undefined` means in-flight, not unavailable.
      renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: undefined,
      })

      expect(invalidateSpy).not.toHaveBeenCalled()
    })

    it('re-arms the attempt budget for a different episode', async () => {
      const { rerender } = renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })
      await flushReconcile(0)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(1)

      rerender({
        episode: episodeAt({ id: 'ep-uuid-2', state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      await flushReconcile(0)
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBe(2)
    })
  })

  describe('the reported bug', () => {
    it('recovers the summary when the task list goes quiet before the state lands', async () => {
      // The exact ordering that defeated the old edge-triggered callback:
      // the task poll reports an empty list first (the non-atomic
      // complete-then-enqueue window), and the episode poll only afterwards
      // reports `summarized`. Nothing observes a "task completed" edge — and
      // the reader must still catch up.
      const { rerender } = renderLiveRefresh(queryClient, {
        episode: episodeAt({ state: 'cleaned', has_summary: false }),
        summaryAvailable: false,
      })

      expect(invalidateSpy).not.toHaveBeenCalled()

      rerender({
        episode: episodeAt({ state: 'summarized', has_summary: true }),
        summaryAvailable: false,
      })

      // The tuple diff fires on the observation itself — no task-completion
      // edge was available to witness, and none was needed.
      expect(countKey(invalidateSpy, SUMMARY_KEY)).toBeGreaterThan(0)
    })
  })
})
