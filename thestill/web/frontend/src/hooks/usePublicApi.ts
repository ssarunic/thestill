// Spec #94 — react-query hooks for the signed-out public pages. A 404 is a
// final answer (the page is not public), so it is never retried.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { PublicNotFound, publicApi } from '../api/public'

const retry = (failureCount: number, error: unknown) => !(error instanceof PublicNotFound) && failureCount < 2
const staleTime = 5 * 60_000

export function usePublicPodcast(slug: string) {
  return useQuery({ queryKey: ['public', 'podcast', slug], queryFn: () => publicApi.podcast(slug), retry, staleTime })
}

export function usePublicPodcastEpisodes(slug: string) {
  return useInfiniteQuery({
    queryKey: ['public', 'podcast-episodes', slug],
    queryFn: ({ pageParam }) => publicApi.podcastEpisodes(slug, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    retry,
    staleTime,
  })
}

export function usePublicEpisode(podcastSlug: string, episodeSlug: string) {
  return useQuery({
    queryKey: ['public', 'episode', podcastSlug, episodeSlug],
    queryFn: () => publicApi.episode(podcastSlug, episodeSlug),
    retry,
    staleTime,
  })
}

export function usePublicSummary(podcastSlug: string, episodeSlug: string) {
  return useQuery({
    queryKey: ['public', 'summary', podcastSlug, episodeSlug],
    queryFn: () => publicApi.summary(podcastSlug, episodeSlug),
    enabled: !!podcastSlug && !!episodeSlug,
    retry,
    staleTime,
  })
}

export function usePublicEpisodeQuotes(podcastSlug: string, episodeSlug: string, q: number | null, speaker: string | null) {
  return useInfiniteQuery({
    queryKey: ['public', 'episode-quotes', podcastSlug, episodeSlug, q, speaker ?? ''],
    queryFn: ({ pageParam }) => publicApi.episodeQuotes(podcastSlug, episodeSlug, q, speaker, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => last.next_page ?? undefined,
    retry,
    staleTime,
  })
}

export function usePublicRecentSummaries(limit = 6) {
  return useQuery({
    queryKey: ['public', 'recent', limit],
    queryFn: () => publicApi.recentSummaries(limit),
    retry,
    staleTime,
  })
}

export function usePublicTopChart(region?: string, category?: string) {
  return useQuery({
    queryKey: ['public', 'top', region ?? '', category ?? ''],
    queryFn: () => publicApi.topChart(region, category),
    retry,
    staleTime,
  })
}

export function usePublicEntity(type: string, slug: string) {
  return useQuery({
    queryKey: ['public', 'entity', type, slug],
    queryFn: () => publicApi.entity(type, slug),
    retry,
    staleTime,
  })
}

export function usePublicEntityQuotes(
  type: string,
  slug: string,
  kind: 'said' | 'about',
  about: string | null,
  enabled = true,
) {
  return useInfiniteQuery({
    queryKey: ['public', 'entity-quotes', type, slug, kind, about ?? ''],
    queryFn: ({ pageParam }) => publicApi.entityQuotes(type, slug, kind, pageParam, about),
    initialPageParam: 1,
    getNextPageParam: (last) => last.next_page ?? undefined,
    enabled,
    retry,
    staleTime,
  })
}

export function usePublicWhoSaidWhat() {
  return useQuery({ queryKey: ['public', 'who-said-what'], queryFn: () => publicApi.whoSaidWhat(), retry, staleTime })
}
