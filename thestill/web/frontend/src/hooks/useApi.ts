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
} from '../api/client'
import type { RefreshRequest, AddPodcastRequest } from '../api/types'

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

export function usePodcast(podcastId: string) {
  return useQuery({
    queryKey: ['podcasts', podcastId],
    queryFn: () => getPodcast(podcastId),
    enabled: !!podcastId,
  })
}

export function usePodcastEpisodes(podcastId: string, limit = 20) {
  return useQuery({
    queryKey: ['podcasts', podcastId, 'episodes', limit],
    queryFn: () => getPodcastEpisodes(podcastId, limit),
    enabled: !!podcastId,
  })
}

export function usePodcastEpisodesInfinite(podcastId: string, limit = 20) {
  return useInfiniteQuery({
    queryKey: ['podcasts', podcastId, 'episodes', 'infinite', limit],
    queryFn: ({ pageParam = 0 }) => getPodcastEpisodes(podcastId, limit, pageParam),
    enabled: !!podcastId,
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset,
  })
}

// Episode hooks
export function useEpisode(episodeId: string) {
  return useQuery({
    queryKey: ['episodes', episodeId],
    queryFn: () => getEpisode(episodeId),
    enabled: !!episodeId,
  })
}

export function useEpisodeTranscript(episodeId: string) {
  return useQuery({
    queryKey: ['episodes', episodeId, 'transcript'],
    queryFn: () => getEpisodeTranscript(episodeId),
    enabled: !!episodeId,
    // Don't poll transcript content
    refetchInterval: false,
    staleTime: 60000, // 1 minute
  })
}

export function useEpisodeSummary(episodeId: string) {
  return useQuery({
    queryKey: ['episodes', episodeId, 'summary'],
    queryFn: () => getEpisodeSummary(episodeId),
    enabled: !!episodeId,
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
