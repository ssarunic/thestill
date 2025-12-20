import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import {
  getDashboardStats,
  getRecentActivity,
  getPodcasts,
  getPodcast,
  getPodcastEpisodes,
  getEpisode,
  getEpisodeTranscript,
  getEpisodeSummary,
} from '../api/client'

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
