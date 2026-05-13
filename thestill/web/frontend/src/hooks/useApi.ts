import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'
import {
  getDashboardStats,
  getNarrationDashboardStats,
  getRecentActivity,
  getPodcasts,
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
  getBriefings,
  getBriefing,
  getLatestBriefing,
  getBriefingContent,
  getBriefingEpisodes,
  previewBriefing,
  createBriefing,
  deleteBriefing,
  getMorningBriefing,
  createMorningBriefing,
  narrateBriefing,
  getNarration,
  quickSearch,
  corpusSearch,
  getEpisodeEntities,
  getEntitySummary,
  getInbox,
  type GetInboxOptions,
} from '../api/client'
import type { RefreshRequest, AddPodcastRequest, PipelineStage, EpisodeFilters, RunPipelineRequest, CreateBriefingRequest, BriefingStatus, DLQBranchFilter, QuickSearchOptions, CorpusSearchOptions, EntityType, NarrateBriefingRequest, KaraokeWordsByEpisode, WordTimestamp } from '../api/types'

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

export function usePodcastsInfinite(limit = 12) {
  return useInfiniteQuery({
    queryKey: ['podcasts', 'infinite', limit],
    queryFn: ({ pageParam = 0 }) => getPodcasts(limit, pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

export function usePodcast(podcastSlug: string) {
  return useQuery({
    queryKey: ['podcasts', podcastSlug],
    queryFn: () => getPodcast(podcastSlug),
    enabled: !!podcastSlug,
  })
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
export function useEpisode(podcastSlug: string, episodeSlug: string) {
  return useQuery({
    queryKey: ['episodes', podcastSlug, episodeSlug],
    queryFn: () => getEpisode(podcastSlug, episodeSlug),
    enabled: !!podcastSlug && !!episodeSlug,
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

export function useEpisodeSummary(podcastSlug: string, episodeSlug: string) {
  return useQuery({
    queryKey: ['episodes', podcastSlug, episodeSlug, 'summary'],
    queryFn: () => getEpisodeSummary(podcastSlug, episodeSlug),
    enabled: !!podcastSlug && !!episodeSlug,
    // Don't poll summary content
    refetchInterval: false,
    staleTime: 60000, // 1 minute
  })
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

export function useEpisodeTasks(episodeId: string | null) {
  return useQuery({
    queryKey: ['episodes', 'tasks', episodeId],
    queryFn: () => getEpisodeTasks(episodeId!),
    enabled: !!episodeId,
    refetchInterval: (query) => {
      // Poll if any task is pending or processing
      const tasks = query.state.data?.tasks || []
      const hasActiveTask = tasks.some((t) => t.status === 'pending' || t.status === 'processing')
      return hasActiveTask ? 2000 : false
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
// Briefing hooks
// ============================================================================

export function useBriefings(limit = 50, status?: BriefingStatus) {
  return useQuery({
    queryKey: ['briefings', limit, status],
    queryFn: () => getBriefings(limit, 0, status),
    refetchInterval: (query) => {
      // Poll every 3 seconds while there are pending or in_progress briefings
      const briefings = query.state.data?.briefings || []
      const hasActiveBriefing = briefings.some(
        (d) => d.status === 'pending' || d.status === 'in_progress'
      )
      return hasActiveBriefing ? 3000 : false
    },
  })
}

export function useBriefingsInfinite(limit = 20, status?: BriefingStatus) {
  return useInfiniteQuery({
    queryKey: ['briefings', 'infinite', limit, status],
    queryFn: ({ pageParam = 0 }) => getBriefings(limit, pageParam, status),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

export function useBriefing(briefingId: string | null) {
  return useQuery({
    queryKey: ['briefings', briefingId],
    queryFn: () => getBriefing(briefingId!),
    enabled: !!briefingId,
  })
}

// Backs the "Today's briefing" card on /inbox. The GET endpoint
// lazy-generates a briefing when eligible; a 404 means "nothing to brief
// about right now" — keep ``retry: false`` so React Query doesn't
// hammer the lazy-generate endpoint on the empty-window path.
// ``staleTime`` matches the backend's perceived "still fresh" window.
export function useLatestBriefing() {
  return useQuery({
    queryKey: ['briefings', 'latest'],
    queryFn: getLatestBriefing,
    staleTime: 60_000,
    retry: false,
  })
}

export function useBriefingContent(briefingId: string | null) {
  return useQuery({
    queryKey: ['briefings', briefingId, 'content'],
    queryFn: () => getBriefingContent(briefingId!),
    enabled: !!briefingId,
    staleTime: 60000, // 1 minute
  })
}

export function useBriefingEpisodes(briefingId: string | null) {
  return useQuery({
    queryKey: ['briefings', briefingId, 'episodes'],
    queryFn: () => getBriefingEpisodes(briefingId!),
    enabled: !!briefingId,
  })
}

export function usePreviewBriefing() {
  return useMutation({
    mutationFn: (request: CreateBriefingRequest) => previewBriefing(request),
  })
}

// Hook for fetching morning briefing count (uses server-configured defaults)
export function useMorningBriefingCount() {
  return useQuery({
    queryKey: ['morning-briefing'],
    queryFn: getMorningBriefing,
    staleTime: 60000, // Cache for 1 minute
  })
}

// Hook for creating morning briefing (uses server-configured defaults)
export function useCreateMorningBriefing() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: createMorningBriefing,
    onSuccess: () => {
      // Invalidate briefings list and morning briefing preview
      queryClient.invalidateQueries({ queryKey: ['briefings'] })
      queryClient.invalidateQueries({ queryKey: ['morning-briefing'] })
    },
  })
}

export function useCreateBriefing() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: CreateBriefingRequest) => createBriefing(request),
    onSuccess: () => {
      // Invalidate briefings list to show the new briefing
      queryClient.invalidateQueries({ queryKey: ['briefings'] })
    },
  })
}

export function useDeleteBriefing() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (briefingId: string) => deleteBriefing(briefingId),
    onSuccess: () => {
      // Invalidate briefings list
      queryClient.invalidateQueries({ queryKey: ['briefings'] })
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
