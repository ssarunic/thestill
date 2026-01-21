import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getDashboardStats,
  getRecentActivity,
  getPodcasts,
  getPodcast,
  getPodcastEpisodes,
  getEpisode,
  getEpisodeTranscript,
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
  getFailedEpisodes,
  getEpisodeFailure,
  retryFailedEpisode,
  runPipeline,
  cancelPipeline,
  unfollowPodcast,
} from '../api/client'
import type { RefreshRequest, AddPodcastRequest, PipelineStage, EpisodeFilters, RunPipelineRequest } from '../api/types'

// Dashboard hooks
export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: getDashboardStats,
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

export function useDLQTasks(limit = 100) {
  return useQuery({
    queryKey: ['dlq', 'tasks', limit],
    queryFn: () => getDLQTasks(limit),
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
