import { useEffect, useMemo, useRef, useState } from 'react'
import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
  type UseQueryOptions,
} from '@tanstack/react-query'
import {
  getDashboardStats,
  getNarrationDashboardStats,
  getRecentActivity,
  getPodcasts,
  getTopPodcasts,
  getPodcast,
  getPodcastEpisodes,
  getEpisode,
  getEpisodeTranscript,
  getEpisodeTranscriptWords,
  getEpisodeSummary,
  startRefresh,
  getRefreshStatus,
  addPodcast,
  getAddPodcastStatus,
  queuePipelineTask,
  getPipelineTaskStatus,
  getEpisodeTasks,
  getAllEpisodes,
  bulkProcessEpisodes,
  getDLQTasks,
  retryDLQTask,
  skipDLQTask,
  retryAllDLQTasks,
  getQueueTasks,
  bumpQueueTask,
  cancelQueueTask,
  getFailedEpisodes,
  getEpisodeFailure,
  retryFailedEpisode,
  runPipeline,
  cancelPipeline,
  followPodcast,
  unfollowPodcast,
  narrateBriefing,
  getBriefings,
  getNarration,
  quickSearch,
  corpusSearch,
  getEpisodeEntities,
  getRelatedEpisodes,
  getEntitySummary,
  getInbox,
  markInboxRead,
  type GetInboxOptions,
  getLatestBriefing,
  getBriefing,
  getBriefingScript,
  markBriefingListened,
} from '../api/client'
import type { RefreshRequest, AddPodcastRequest, PipelineStage, EpisodeFilters, RunPipelineRequest, DLQBranchFilter, QuickSearchOptions, CorpusSearchOptions, EntityType, NarrateBriefingRequest, KaraokeWordsByEpisode, WordTimestamp, EpisodeDetail, EpisodeTasksResponse } from '../api/types'

// Dashboard hooks
export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: getDashboardStats,
  })
}

export function useNarrationDashboardStats() {
  return useQuery({
    queryKey: ['dashboard', 'narration'],
    queryFn: getNarrationDashboardStats,
    staleTime: 60_000,
  })
}

export function useRecentActivity(limit = 10) {
  return useQuery({
    queryKey: ['dashboard', 'activity', limit],
    queryFn: () => getRecentActivity(limit),
  })
}

export function useRecentActivityInfinite(limit = 10) {
  return useInfiniteQuery({
    queryKey: ['dashboard', 'activity', 'infinite', limit],
    queryFn: ({ pageParam = 0 }) => getRecentActivity(limit, pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

// Podcast hooks
export function usePodcasts() {
  return useQuery({
    queryKey: ['podcasts'],
    queryFn: () => getPodcasts(),
  })
}

export function usePodcastsInfinite(limit = 12, q?: string) {
  return useInfiniteQuery({
    queryKey: ['podcasts', 'infinite', limit, q ?? ''],
    queryFn: ({ pageParam = 0 }) => getPodcasts(limit, pageParam, q),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

// Top-podcast charts (spec #57). Cached by (region, q, category) so returning
// to the page — e.g. via the browser Back button from a podcast detail —
// re-renders instantly from cache, letting the browser restore the scroll
// position instead of dumping the user at the top. ``placeholderData`` keeps
// the previous list on screen while a new filter's request lands, so the list
// height (and thus scroll) doesn't collapse mid-transition.
export function useTopPodcasts(region?: string, q?: string, category?: string) {
  return useQuery({
    queryKey: ['top-podcasts', region ?? '', q ?? '', category ?? ''],
    queryFn: () => getTopPodcasts(region, 500, q, category),
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  })
}

export function usePodcast(podcastSlug: string) {
  return useQuery({
    queryKey: ['podcasts', podcastSlug],
    queryFn: () => getPodcast(podcastSlug),
    enabled: !!podcastSlug,
    // Spec #74 — opening the page may have enqueued a feed refresh. Poll
    // while the server says one is pending so newly discovered episodes
    // land without a reload; level-gated on the flag, never on task activity.
    refetchInterval: (query) => (query.state.data?.podcast?.refresh_pending ? 5_000 : false),
  })
}

/**
 * Spec #74 — when the open-triggered refresh settles (`refresh_pending`
 * flips true → false) invalidate the episode list so the rows it discovered
 * render. A page that was never pending never invalidates.
 */
export function useInvalidateEpisodesWhenRefreshSettles(
  podcastSlug: string,
  refreshPending: boolean | undefined,
) {
  const queryClient = useQueryClient()
  const wasPending = useRef(false)
  useEffect(() => {
    if (refreshPending) {
      wasPending.current = true
      return
    }
    if (wasPending.current) {
      wasPending.current = false
      queryClient.invalidateQueries({ queryKey: ['podcasts', podcastSlug, 'episodes'] })
    }
  }, [refreshPending, podcastSlug, queryClient])
}

export function usePodcastEpisodes(podcastSlug: string, limit = 20) {
  return useQuery({
    queryKey: ['podcasts', podcastSlug, 'episodes', limit],
    queryFn: () => getPodcastEpisodes(podcastSlug, limit),
    enabled: !!podcastSlug,
  })
}

export function usePodcastEpisodesInfinite(podcastSlug: string, limit = 20) {
  return useInfiniteQuery({
    queryKey: ['podcasts', podcastSlug, 'episodes', 'infinite', limit],
    queryFn: ({ pageParam = 0 }) => getPodcastEpisodes(podcastSlug, limit, pageParam),
    enabled: !!podcastSlug,
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

export function useFollowPodcast() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (podcastSlug: string) => followPodcast(podcastSlug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useUnfollowPodcast() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (podcastSlug: string) => unfollowPodcast(podcastSlug),
    onSuccess: () => {
      // Invalidate podcasts list to refresh the UI
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// Episode hooks
export interface UseEpisodeOptions {
  /**
   * Spec #68 D1/Phase 2 — keep the 5s clock running. Pass `false` only once
   * the episode is settled (terminal state *and* its content actually
   * reconciled); see `useEpisodeLiveRefresh`'s `settled` return value.
   */
  live?: boolean
}

export function useEpisode(
  podcastSlug: string,
  episodeSlug: string,
  { live = true }: UseEpisodeOptions = {},
) {
  return useQuery({
    queryKey: ['episodes', podcastSlug, episodeSlug],
    queryFn: () => getEpisode(podcastSlug, episodeSlug),
    enabled: !!podcastSlug && !!episodeSlug,
    // Spec #68 D1 — this query is the reader's clock: `useEpisodeLiveRefresh`
    // derives content freshness from it. While the episode is unsettled it
    // polls every 5s and runs in background tabs too, since a 40-minute
    // transcription with the tab backgrounded is the common case. Content
    // queries stay focus-gated. (Spec #69 Phase 3 removed the app-wide 5s
    // default this clock used to inherit — the interval now lives here,
    // explicitly.)
    //
    // It is pointedly NOT gated on *task activity* — a 2s task poll returning
    // an empty list before the 5s episode poll returns `summarized` would
    // latch the clock off with the cache still on `cleaned`, which is the
    // original stale-summary bug with extra steps. It is gated only on the
    // episode having actually arrived at its terminal content, which is a
    // level, not an edge.
    //
    // Once settled the poll stops: a reader left open on a finished episode
    // would otherwise cost ~17k requests/day. Reactivation is
    // `refetchOnWindowFocus` (staleTime stays 5s here — the app default is
    // 60s — so returning to the tab refetches) plus explicit invalidation
    // from any queue mutation.
    staleTime: 5_000,
    refetchInterval: live ? 5_000 : (false as const),
    refetchIntervalInBackground: live,
  })
}

export function useEpisodeTranscript(podcastSlug: string, episodeSlug: string) {
  return useQuery({
    queryKey: ['episodes', podcastSlug, episodeSlug, 'transcript'],
    queryFn: () => getEpisodeTranscript(podcastSlug, episodeSlug),
    enabled: !!podcastSlug && !!episodeSlug,
    // Don't poll transcript content
    refetchInterval: false,
    staleTime: 60000, // 1 minute
  })
}

// Spec #38 — karaoke wipe word-level timestamps. ``enabled`` is driven by
// the toolbar chip; the request never fires when karaoke is off, so the
// default page weight is unchanged. ``data === null`` means the endpoint
// returned 404 (no word data for this episode) — the chip renders
// disabled with a tooltip in that case. The response is pre-indexed into
// a Map so the viewer doesn't repeat the work per render.
export function useEpisodeTranscriptWords(
  podcastSlug: string,
  episodeSlug: string,
  enabled: boolean,
) {
  return useQuery<KaraokeWordsByEpisode | null>({
    queryKey: ['episodes', podcastSlug, episodeSlug, 'transcript', 'words'],
    queryFn: async () => {
      const response = await getEpisodeTranscriptWords(podcastSlug, episodeSlug)
      if (response === null) return null
      const wordsBySegmentId = new Map<number, WordTimestamp[]>()
      for (const seg of response.segments) {
        wordsBySegmentId.set(seg.segment_id, seg.words)
      }
      return {
        episodeId: response.episode_id,
        offset: response.playback_time_offset_seconds,
        wordsBySegmentId,
      }
    },
    enabled: enabled && !!podcastSlug && !!episodeSlug,
    refetchInterval: false,
    // Word timestamps don't change once produced; keep them around for
    // the whole session.
    staleTime: 5 * 60_000,
  })
}

export function useEpisodeSummary(podcastSlug: string, episodeSlug: string, lang?: string) {
  return useQuery({
    queryKey: ['episodes', podcastSlug, episodeSlug, 'summary', lang ?? 'original'],
    queryFn: () => getEpisodeSummary(podcastSlug, episodeSlug, lang),
    enabled: !!podcastSlug && !!episodeSlug,
    // Keep the language control visible while a first-time translation is
    // generated; EpisodeReader replaces the content itself with a skeleton.
    placeholderData: (previousData) => previousData,
    // Don't poll summary content
    refetchInterval: false,
    staleTime: 60000, // 1 minute
  })
}

// Spec #68 D3(b) — bounded reconciliation retries. Three attempts spread over
// ~12s covers a transient refetch failure without spinning on the legitimately
// permanent `has_summary && !available` an N/A summary produces (spec #41).
const RECONCILE_MAX_ATTEMPTS = 3
const RECONCILE_BACKOFF_MS = [0, 3_000, 9_000]

export interface EpisodeLiveRefreshArgs {
  podcastSlug: string | undefined
  episodeSlug: string | undefined
  episode: EpisodeDetail | undefined
  /** `available` from the transcript query; `undefined` while it loads. */
  transcriptAvailable: boolean | undefined
  /** `available` from the summary query; `undefined` while it loads. */
  summaryAvailable: boolean | undefined
}

/**
 * Spec #68 D3 — keeps the reader's frozen content queries in step with the
 * live episode query.
 *
 * `useEpisodeTranscript` / `useEpisodeSummary` opt out of the app-wide poll
 * (`refetchInterval: false`, `staleTime: 60_000`), so something has to
 * reconcile them against `useEpisode`, which polls every 5s. That job used
 * to belong to an edge-triggered callback in `PipelineActionButton` — it
 * fired only if the client witnessed the exact moment the task list went
 * quiet, and it lived in a component that unmounts at `summarized`, i.e.
 * on the very transition it existed to catch.
 *
 * This hook watches a *level* the server re-asserts on every 5s tick
 * instead, so a missed observation costs latency rather than correctness.
 */
export function useEpisodeLiveRefresh({
  podcastSlug,
  episodeSlug,
  episode,
  transcriptAvailable,
  summaryAvailable,
}: EpisodeLiveRefreshArgs) {
  const queryClient = useQueryClient()

  // Memoised: these are effect dependencies, and a fresh array each render
  // would re-run both effects on every render of the reader.
  const transcriptKey = useMemo(
    () => ['episodes', podcastSlug, episodeSlug, 'transcript'],
    [podcastSlug, episodeSlug],
  )
  const summaryKey = useMemo(
    () => ['episodes', podcastSlug, episodeSlug, 'summary'],
    [podcastSlug, episodeSlug],
  )

  // (a) Invalidate on any *change*, not on a false→true flip. `has_transcript`
  // and `has_summary` are `bool(path)` server-side and re-transcription clears
  // both downstream paths ([task_handlers.py:472-473]), so they legitimately
  // travel true→false→true. Diffing the tuple handles regeneration in both
  // directions — a true→false transition should also drop now-stale content.
  const prevRef = useRef<{
    id: string
    state: string
    hasTranscript: boolean
    hasSummary: boolean
  } | null>(null)
  useEffect(() => {
    if (!episode || !podcastSlug || !episodeSlug) return

    const prev = prevRef.current
    prevRef.current = {
      id: episode.id,
      state: episode.state,
      hasTranscript: episode.has_transcript,
      hasSummary: episode.has_summary,
    }

    // First observation of this episode (or a different one — navigation, not
    // progress): its content queries were fetched alongside it, so there is
    // nothing to reconcile yet.
    if (!prev || prev.id !== episode.id) return

    if (prev.hasTranscript !== episode.has_transcript) {
      // Prefix key — also covers the spec #38 word-timestamp sidecar.
      queryClient.invalidateQueries({ queryKey: transcriptKey })
    }
    if (prev.hasSummary !== episode.has_summary) {
      // Prefix key — every cached language variant, not just the active one
      // (the full key is [..., 'summary', lang]).
      queryClient.invalidateQueries({ queryKey: summaryKey })
    }
    if (prev.state !== episode.state) {
      queryClient.invalidateQueries({ queryKey: ['episodes', episode.id, 'entities'] })
      queryClient.invalidateQueries({ queryKey: ['episodes', episode.id, 'related'] })
      // Re-arm the task poll if it went quiet while work was queued elsewhere
      // (CLI, scheduler, another client).
      queryClient.invalidateQueries({ queryKey: ['episodes', 'tasks', episode.id] })
    }
  }, [episode, podcastSlug, episodeSlug, queryClient, transcriptKey, summaryKey])

  // (b) Mount reconciliation, with bounded retries. Covers the case where the
  // flag was already true before this reader mounted, so (a) has no transition
  // to observe — e.g. back-navigation into a 60s-fresh cached
  // `available: false`.
  //
  // Retries matter because an invalidation is not a guarantee: the refetch can
  // fail, or briefly return the same unavailable response. Marking the artifact
  // reconciled at *request* time would spend the single attempt on a failure
  // and leave the reader stale until focus or reload — the very state this
  // spec exists to eliminate. So an attempt only counts once it has happened,
  // and a still-unavailable result schedules the next one with backoff.
  //
  // The cap is what keeps this terminating: `available` is
  // `not summary.startswith("N/A")` server-side, so an N/A summary (spec #41)
  // is a legitimate *permanent* `has_summary: true && available: false`. An
  // uncapped mismatch-driven retry would spin forever on those episodes.
  const attemptsRef = useRef(new Map<string, number>())
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const [attemptTick, bumpAttemptTick] = useState(0)

  useEffect(() => {
    if (!episode || !podcastSlug || !episodeSlug) return

    const timers = timersRef.current
    const attempts = attemptsRef.current

    const artifacts = [
      { name: 'transcript', present: episode.has_transcript, available: transcriptAvailable, key: transcriptKey },
      { name: 'summary', present: episode.has_summary, available: summaryAvailable, key: summaryKey },
    ] as const

    for (const artifact of artifacts) {
      const slot = `${episode.id}:${artifact.name}`

      // Reconciled (or nothing to reconcile): reset so a later regeneration
      // gets a fresh budget rather than inheriting an exhausted one.
      if (!artifact.present || artifact.available !== false) {
        attempts.delete(slot)
        continue
      }
      if ((attempts.get(slot) ?? 0) >= RECONCILE_MAX_ATTEMPTS) continue
      if (timers.has(slot)) continue // an attempt is already in flight

      const delay = RECONCILE_BACKOFF_MS[attempts.get(slot) ?? 0]
      timers.set(
        slot,
        setTimeout(() => {
          timers.delete(slot)
          attempts.set(slot, (attempts.get(slot) ?? 0) + 1)
          queryClient.invalidateQueries({ queryKey: artifact.key })
          // Re-run this effect once the attempt has actually been made, so a
          // still-unavailable response can schedule the next one.
          bumpAttemptTick((tick: number) => tick + 1)
        }, delay),
      )
    }

    return () => {
      for (const timer of timers.values()) clearTimeout(timer)
      timers.clear()
    }
  }, [
    episode,
    podcastSlug,
    episodeSlug,
    transcriptAvailable,
    summaryAvailable,
    queryClient,
    transcriptKey,
    summaryKey,
    // Re-runs the effect after each completed attempt so a still-unavailable
    // artifact can schedule the next one. Without this the backoff chain
    // stops dead after attempt 1 and the budget is never spent.
    attemptTick,
  ])

  // Spec #68 Phase 2 — "settled" means terminal *and* the content actually
  // arrived, not merely terminal. Gating the episode poll on state alone would
  // stop the clock while the summary was still catching up.
  const contentTerminal = episode?.state === 'summarized' || episode?.is_failed === true
  const contentArrived =
    (!episode?.has_summary || summaryAvailable !== undefined) &&
    (!episode?.has_transcript || transcriptAvailable !== undefined)

  return { settled: Boolean(episode) && contentTerminal && contentArrived, contentTerminal }
}

// Commands hooks
export function useRefreshStatus(enabled = true) {
  return useQuery({
    queryKey: ['commands', 'refresh', 'status'],
    queryFn: getRefreshStatus,
    enabled,
    refetchInterval: (query) => {
      // Poll every 1 second while running, stop when complete
      const status = query.state.data?.status
      return status === 'running' ? 1000 : false
    },
  })
}

export function useStartRefresh() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: RefreshRequest = {}) => startRefresh(request),
    onSuccess: () => {
      // Start polling the status
      queryClient.invalidateQueries({ queryKey: ['commands', 'refresh', 'status'] })
    },
    onSettled: () => {
      // When refresh completes, invalidate dashboard data to show new episodes
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
    },
  })
}

// Add Podcast hooks
export function useAddPodcastStatus(enabled = true) {
  return useQuery({
    queryKey: ['commands', 'add', 'status'],
    queryFn: getAddPodcastStatus,
    enabled,
    refetchInterval: (query) => {
      // Poll every 1 second while running, stop when complete
      const status = query.state.data?.status
      return status === 'running' ? 1000 : false
    },
  })
}

export function useAddPodcast() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: AddPodcastRequest) => addPodcast(request),
    onSuccess: () => {
      // Start polling the status
      queryClient.invalidateQueries({ queryKey: ['commands', 'add', 'status'] })
    },
    onSettled: () => {
      // When add completes, invalidate podcasts list to show new podcast
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// Pipeline Task hooks (Queue-based)
export function useQueuePipelineTask(podcastSlug: string, episodeSlug: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (stage: PipelineStage) =>
      queuePipelineTask(stage, { podcast_slug: podcastSlug, episode_slug: episodeSlug }),
    onSuccess: () => {
      // Invalidate episode data to refresh state
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug] })
      // Also invalidate episode tasks
      queryClient.invalidateQueries({ queryKey: ['episodes', 'tasks'] })
    },
  })
}

export function usePipelineTaskStatus(taskId: string | null) {
  return useQuery({
    queryKey: ['commands', 'pipeline', 'task', taskId],
    queryFn: () => getPipelineTaskStatus(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      // Poll while task is pending or processing
      const status = query.state.data?.status
      return status === 'pending' || status === 'processing' ? 2000 : false
    },
  })
}

// Spec #68 D2 — task-poll cadence.
const ACTIVE_TASK_POLL_MS = 2_000
const IDLE_TASK_POLL_MS = 15_000
// Non-terminal episodes never go fully silent: work can be queued by a
// scheduler, the CLI, or another client, and — crucially — an externally
// started long stage produces NO local signal until it completes (episode
// state changes on completion, not on start). Without this probe the stepper
// would show "nothing running" for the entire duration of someone else's
// transcription. One request per minute, only while the reader is open.
const PROBE_TASK_POLL_MS = 60_000
// 4 idle ticks at the idle cadence ≈ 1 minute of grace after the last
// observable activity. Sized to cover the entity branch past `reindex` —
// notably #46's `compute-related`, which repopulates the Related-episodes
// rail on the reader itself — without watching the coalesced,
// network-bound tail (`enrich-entities`) that no per-episode surface renders.
const IDLE_TICKS_BEFORE_BACKOFF = 4

// `retry_scheduled` counts as active: the task is coming back on its own,
// so the chain has not gone quiet.
const ACTIVE_TASK_STATUSES = new Set<string>(['pending', 'processing', 'retry_scheduled'])

function hasActiveTask(data: EpisodeTasksResponse | undefined): boolean {
  return (data?.tasks ?? []).some((t) => ACTIVE_TASK_STATUSES.has(t.status))
}

export interface UseEpisodeTasksOptions {
  /**
   * Terminal for *content* purposes (`summarized` / failed). Only a terminal
   * episode is allowed to stop polling outright; anything else falls back to
   * the slow probe. Deliberately not "no active task" — that stops inside the
   * gap before the entity branch is enqueued.
   */
  contentTerminal?: boolean
}

/**
 * Spec #68 D2 — episode task list on a self-healing cadence.
 *
 * The previous implementation returned `false` from `refetchInterval` the
 * instant a response contained no active task, which is a latch that can
 * only fall open: `TaskWorker` completes a task and only *then* enqueues
 * its successor ([task_worker.py:671-704]), so a poll landing in that
 * non-atomic window sees an empty list while the pipeline is still running
 * — and nothing ever restarts the query.
 *
 * Instead the poll drops to a slow tier and only stops after
 * `IDLE_TICKS_BEFORE_STOP` *consecutive* quiet ticks. Any active task
 * resets the counter, so an inter-stage gap costs one slow tick rather
 * than starting a countdown to silence.
 *
 * Stopping is safe because every path back to activity restarts it: a
 * queue mutation invalidates `['episodes', 'tasks']`, `useEpisodeLiveRefresh`
 * invalidates on any episode-state change, and a window refocus refetches.
 */
export function useEpisodeTasks(
  episodeId: string | null,
  { contentTerminal = false }: UseEpisodeTasksOptions = {},
) {
  // Counted in `queryFn` rather than in an effect or in `refetchInterval`
  // itself. `queryFn` runs exactly once per fetch and completes before React
  // Query recomputes the interval, so the count is never double-incremented
  // (as it would be in `refetchInterval`, which can be evaluated more than
  // once per fetch) and never a render behind (as it would be in an effect,
  // which commits after the interval is scheduled — worth one extra poll).
  //
  // This is observer-local state over a shared cache entry, which is only
  // sound because the reader mounts exactly one owner (`EpisodeReader`) and
  // passes the result down. Mounting a second observer would split the
  // counter: whichever observer's `queryFn` loses a collided fetch does not
  // advance, so "4 consecutive idle responses" would stop being a property of
  // the query. Keyed by episode id so navigation resets the grace period.
  const idleRef = useRef<{ episodeId: string | null; ticks: number }>({
    episodeId: null,
    ticks: 0,
  })

  return useQuery({
    queryKey: ['episodes', 'tasks', episodeId],
    queryFn: async () => {
      const data = await getEpisodeTasks(episodeId!)
      const carried = idleRef.current.episodeId === episodeId ? idleRef.current.ticks : 0
      idleRef.current = {
        episodeId,
        ticks: hasActiveTask(data) ? 0 : carried + 1,
      }
      return data
    },
    enabled: !!episodeId,
    refetchInterval: (query) => {
      if (hasActiveTask(query.state.data)) return ACTIVE_TASK_POLL_MS
      if (idleRef.current.ticks < IDLE_TICKS_BEFORE_BACKOFF) return IDLE_TASK_POLL_MS
      // Past the grace period: a settled episode goes silent, an unsettled one
      // keeps a slow probe so externally queued work is still discovered.
      return contentTerminal ? false : PROBE_TASK_POLL_MS
    },
  })
}

// Episode Browser hooks (cross-podcast)
export function useAllEpisodesInfinite(filters: EpisodeFilters, limit = 20) {
  return useInfiniteQuery({
    queryKey: ['episodes', 'all', 'infinite', filters, limit],
    queryFn: ({ pageParam = 0 }) => getAllEpisodes(limit, pageParam, filters),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

export function useBulkProcess() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (episodeIds: string[]) => bulkProcessEpisodes(episodeIds),
    onSuccess: () => {
      // Invalidate all episode-related queries to refresh states
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['podcasts'] })
    },
  })
}

// ============================================================================
// Dead Letter Queue (DLQ) hooks
// ============================================================================

export function useDLQTasks(limit = 100, branch: DLQBranchFilter = 'all') {
  return useQuery({
    queryKey: ['dlq', 'tasks', limit, branch],
    queryFn: () => getDLQTasks(limit, branch),
    refetchInterval: 10000, // Poll every 10 seconds
  })
}

export function useRetryDLQTask() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (taskId: string) => retryDLQTask(taskId),
    onSuccess: () => {
      // Invalidate DLQ and episode data
      queryClient.invalidateQueries({ queryKey: ['dlq'] })
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useSkipDLQTask() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (taskId: string) => skipDLQTask(taskId),
    onSuccess: () => {
      // Invalidate DLQ data
      queryClient.invalidateQueries({ queryKey: ['dlq'] })
    },
  })
}

export function useRetryAllDLQTasks() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (taskIds?: string[]) => retryAllDLQTasks(taskIds),
    onSuccess: () => {
      // Invalidate DLQ and episode data
      queryClient.invalidateQueries({ queryKey: ['dlq'] })
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// ============================================================================
// Queue Viewer hooks
// ============================================================================

export function useQueueTasks(completedLimit = 10) {
  return useQuery({
    queryKey: ['queue', 'tasks', completedLimit],
    queryFn: () => getQueueTasks(completedLimit),
    refetchInterval: (query) => {
      // Poll every 5 seconds while there are active tasks, 15 seconds when idle
      const data = query.state.data
      if (!data) return 5000 // Poll while loading
      const hasActiveTasks =
        data.processing_tasks.length > 0 || data.pending_count > 0 || data.retry_scheduled_count > 0
      return hasActiveTasks ? 5000 : 15000
    },
  })
}

export type EpisodeActiveStage = {
  stage: PipelineStage
  status: 'queued' | 'processing'
}

export function useProcessingStageByEpisodeId(): Map<string, EpisodeActiveStage> {
  const { data } = useQueueTasks()
  const map = new Map<string, EpisodeActiveStage>()
  // Pending first, processing wins if both exist for the same episode
  for (const task of data?.pending_tasks ?? []) {
    map.set(task.episode_id, { stage: task.stage, status: 'queued' })
  }
  for (const task of data?.processing_tasks ?? []) {
    map.set(task.episode_id, { stage: task.stage, status: 'processing' })
  }
  return map
}

export function useBumpQueueTask() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (taskId: string) => bumpQueueTask(taskId),
    onSuccess: () => {
      // Invalidate queue data to refresh task order
      queryClient.invalidateQueries({ queryKey: ['queue'] })
    },
  })
}

export function useCancelQueueTask() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (taskId: string) => cancelQueueTask(taskId),
    onSuccess: () => {
      // Invalidate queue data to refresh task list
      queryClient.invalidateQueries({ queryKey: ['queue'] })
    },
  })
}

// ============================================================================
// Episode Failure hooks
// ============================================================================

export function useFailedEpisodes(limit = 100) {
  return useQuery({
    queryKey: ['episodes', 'failed', limit],
    queryFn: () => getFailedEpisodes(limit),
    refetchInterval: 10000, // Poll every 10 seconds
  })
}

export function useEpisodeFailure(episodeId: string | null) {
  return useQuery({
    queryKey: ['episodes', episodeId, 'failure'],
    queryFn: () => getEpisodeFailure(episodeId!),
    enabled: !!episodeId,
  })
}

export function useRetryFailedEpisode() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (episodeId: string) => retryFailedEpisode(episodeId),
    onSuccess: () => {
      // Invalidate episode and failure data
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      queryClient.invalidateQueries({ queryKey: ['dlq'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// ============================================================================
// Full Pipeline hooks
// ============================================================================

export function useRunPipeline(podcastSlug: string, episodeSlug: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (targetState?: string) =>
      runPipeline({
        podcast_slug: podcastSlug,
        episode_slug: episodeSlug,
        target_state: targetState,
      } as RunPipelineRequest),
    onSuccess: () => {
      // Invalidate episode data to refresh state
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug] })
      // Also invalidate episode tasks
      queryClient.invalidateQueries({ queryKey: ['episodes', 'tasks'] })
    },
  })
}

export function useCancelPipeline() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (episodeId: string) => cancelPipeline(episodeId),
    onSuccess: () => {
      // Invalidate episode and task data
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// ============================================================================
// Narration hooks (spec #33)
// ============================================================================

export function useNarration(narrationId: string | null) {
  return useQuery({
    queryKey: ['narrations', narrationId],
    queryFn: () => getNarration(narrationId!),
    enabled: !!narrationId,
    staleTime: 60_000,
  })
}

export function useNarrateBriefing() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ briefingId, request }: { briefingId: string; request: NarrateBriefingRequest }) =>
      narrateBriefing(briefingId, request),
    onSuccess: (data, { briefingId }) => {
      // Refresh the briefing detail so the new variant appears in the
      // ``narrations`` list, and the targeted narration query so the
      // reader picks up the new markdown if it was already cached.
      queryClient.invalidateQueries({ queryKey: ['briefings', briefingId] })
      queryClient.invalidateQueries({
        queryKey: ['narrations', data.narration_id],
      })
    },
  })
}

// ============================================================================
// Search hooks (spec #28 §4)
// ============================================================================

// Quick typeahead. Stays disabled below 2 chars so we don't pummel
// the backend on the first keystroke. `keepPreviousData` makes the
// dropdown feel stable while the next request lands.
export function useQuickSearch(query: string, options: QuickSearchOptions = {}) {
  const trimmed = query.trim()
  return useQuery({
    queryKey: ['search', 'quick', trimmed, options],
    queryFn: ({ signal }) => quickSearch(trimmed, options, signal),
    enabled: trimmed.length >= 2,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  })
}

// Full corpus search for the /search results page (Phase 4.2). Hybrid
// is the default — that's the LLM-friendly mode and is fine here
// because typing latency isn't on the line.
export function useCorpusSearch(query: string, options: CorpusSearchOptions = {}) {
  const trimmed = query.trim()
  return useQuery({
    queryKey: ['search', 'corpus', trimmed, options],
    queryFn: ({ signal }) => corpusSearch(trimmed, options, signal),
    enabled: trimmed.length >= 2,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  })
}

// Spec #28 §5.2 — episode-page entity UX. The episode endpoint is
// fetched once per episode and feeds the strip + rail + inline
// highlights + filter bar from a single payload, so the components
// don't each issue their own request.
export function useEpisodeEntities(episodeId: string | null | undefined, minConfidence = 0) {
  return useQuery({
    queryKey: ['episodes', episodeId, 'entities', minConfidence],
    queryFn: () => getEpisodeEntities(episodeId!, minConfidence),
    enabled: !!episodeId,
    staleTime: 60_000,
  })
}

// Spec #28 §5.2 — "Related episodes" rail. Results are stable once the
// corpus is indexed, so a 5-minute staleTime keeps the rail snappy on
// back-navigation without re-querying the centroid each visit.
export function useRelatedEpisodes(episodeId: string | null | undefined, limit = 5) {
  return useQuery({
    queryKey: ['episodes', episodeId, 'related', limit],
    queryFn: () => getRelatedEpisodes(episodeId!, limit),
    enabled: !!episodeId,
    staleTime: 5 * 60_000,
  })
}

export function useEntitySummary(entityType: EntityType | null, idSlug: string | null) {
  return useQuery({
    queryKey: ['entities', entityType, idSlug],
    queryFn: () => getEntitySummary(entityType!, idSlug!),
    enabled: !!entityType && !!idSlug,
    staleTime: 60_000,
  })
}

export interface UseInboxOptions extends GetInboxOptions {
  /**
   * Forwarded to react-query so callers can poll while imports are still
   * working through the pipeline. Pass a function that inspects the query
   * to decide whether to keep polling.
   */
  refetchInterval?: UseQueryOptions<
    Awaited<ReturnType<typeof getInbox>>
  >['refetchInterval']
}

export function useInbox({ refetchInterval, ...options }: UseInboxOptions = {}) {
  return useQuery({
    queryKey: ['inbox', options.state ?? null, options.limit ?? null, options.before ?? null],
    queryFn: () => getInbox(options),
    staleTime: 15_000,
    refetchInterval,
  })
}

export interface UseInboxInfiniteOptions extends Omit<GetInboxOptions, 'before'> {
  /** Conditional poll while imports work through the pipeline (see Inbox.tsx). */
  refetchInterval?:
    | number
    | false
    | ((query: {
        state: { data?: InfiniteData<Awaited<ReturnType<typeof getInbox>>> }
      }) => number | false | undefined)
}

/**
 * Cursor-paged inbox (spec #69 Phase 3 rode-along fix): the server caps
 * each page at 50 and returns a ``next_before`` cursor, but the page-less
 * `useInbox` call meant anything older than the newest 50 was unreachable.
 * Pages chain on ``before`` = previous page's ``next_before``.
 */
export function useInboxInfinite({ refetchInterval, ...options }: UseInboxInfiniteOptions = {}) {
  return useInfiniteQuery({
    queryKey: ['inbox', 'infinite', options.state ?? null, options.limit ?? null],
    queryFn: ({ pageParam }) => getInbox({ ...options, before: pageParam || undefined }),
    initialPageParam: '',
    getNextPageParam: (lastPage) => lastPage.next_before ?? undefined,
    staleTime: 15_000,
    refetchInterval,
  })
}

export function useMarkInboxRead() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (episodeId: string) => markInboxRead(episodeId),
    onSuccess: (data) => {
      // ``marked: false`` means nothing changed server-side (no inbox row,
      // or the row already left unread) — skip the refetch churn.
      if (data.marked) {
        queryClient.invalidateQueries({ queryKey: ['inbox'] })
      }
    },
  })
}

/**
 * View-driven read tracking (spec #29): mark the episode's inbox row read
 * once per viewed episode, but only after a summary is actually available —
 * glancing at a page that still says "Transcribing…" doesn't count as
 * reading. Fire-and-forget: the server treats a missing inbox row as a
 * no-op, and a network failure just leaves the row unread for a later view.
 */
export function useMarkInboxReadOnView(
  episodeId: string | null | undefined,
  summaryAvailable: boolean,
) {
  const { mutate } = useMarkInboxRead()
  const markedEpisodeRef = useRef<string | null>(null)
  useEffect(() => {
    if (!episodeId || !summaryAvailable) return
    if (markedEpisodeRef.current === episodeId) return
    markedEpisodeRef.current = episodeId
    mutate(episodeId)
  }, [episodeId, summaryAvailable, mutate])
}

// Per-user briefings (spec #36). The "latest" endpoint lazy-generates,
// so a 404 means "nothing eligible to brief about right now" — callers
// should treat it as a hide-the-card signal rather than an error.
export function useLatestBriefing() {
  return useQuery({
    queryKey: ['briefings', 'latest'],
    queryFn: () => getLatestBriefing(),
    staleTime: 60_000,
    retry: false,
  })
}

export function useGenerateBriefingNow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => getLatestBriefing(true),
    onSuccess: (data) => {
      queryClient.setQueryData(['briefings', 'latest'], data)
      queryClient.invalidateQueries({ queryKey: ['briefings', 'infinite'] })
    },
  })
}

export function useBriefingsInfinite(limit = 20) {
  return useInfiniteQuery({
    queryKey: ['briefings', 'infinite', limit],
    queryFn: ({ pageParam = 0 }) => getBriefings(limit, pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

export function useBriefing(briefingId: string | null) {
  return useQuery({
    queryKey: ['briefings', briefingId],
    queryFn: () => getBriefing(briefingId!),
    enabled: !!briefingId,
    staleTime: 60_000,
  })
}

export function useBriefingScript(briefingId: string | null) {
  return useQuery({
    queryKey: ['briefings', briefingId, 'script'],
    queryFn: () => getBriefingScript(briefingId!),
    enabled: !!briefingId,
    staleTime: 5 * 60_000,
  })
}

export function useMarkBriefingListened() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (briefingId: string) => markBriefingListened(briefingId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['briefings', 'latest'] })
      queryClient.invalidateQueries({ queryKey: ['briefings', data.id] })
    },
  })
}
