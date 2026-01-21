import { useParams, Link, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback } from 'react'
import { usePodcast, usePodcastEpisodesInfinite, useUnfollowPodcast } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import EpisodeCard from '../components/EpisodeCard'
import ExpandableDescription from '../components/ExpandableDescription'
import Button, { MinusIcon } from '../components/Button'

export default function PodcastDetail() {
  const { podcastSlug } = useParams<{ podcastSlug: string }>()
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [isUnfollowing, setIsUnfollowing] = useState(false)
  const { data: podcastData, isLoading: podcastLoading, error: podcastError } = usePodcast(podcastSlug!)
  const { mutate: unfollow } = useUnfollowPodcast()
  const {
    data: episodesData,
    isLoading: episodesLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = usePodcastEpisodesInfinite(podcastSlug!)

  // Scroll to top when entering this page
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [podcastSlug])

  // Intersection Observer for infinite scroll
  const loadMoreRef = useRef<HTMLDivElement>(null)

  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const [entry] = entries
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage]
  )

  useEffect(() => {
    const element = loadMoreRef.current
    if (!element) return

    const observer = new IntersectionObserver(handleObserver, {
      root: null,
      rootMargin: '100px',
      threshold: 0,
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [handleObserver])

  // Flatten all pages into a single episodes array
  const allEpisodes = episodesData?.pages.flatMap((page) => page.episodes) ?? []
  const totalEpisodes = episodesData?.pages[0]?.total ?? 0

  if (podcastError) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading podcast</h2>
          <p className="text-red-600 text-sm">{podcastError.message}</p>
          <Link to="/podcasts" className="mt-4 inline-block text-primary-600 hover:underline">
            ← Back to podcasts
          </Link>
        </div>
      </div>
    )
  }

  const podcast = podcastData?.podcast

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <nav className="text-sm">
        <Link to="/podcasts" className="text-gray-500 hover:text-gray-700">Podcasts</Link>
        <span className="mx-2 text-gray-400">/</span>
        <span className="text-gray-900">{podcastLoading ? '...' : podcast?.title}</span>
      </nav>

      {/* Header */}
      {podcastLoading ? (
        <div className="animate-pulse bg-white rounded-lg border border-gray-200 p-4 sm:p-6">
          <div className="flex flex-col sm:flex-row gap-4 sm:gap-6">
            <div className="w-20 h-20 sm:w-24 sm:h-24 bg-gray-200 rounded-lg mx-auto sm:mx-0 flex-shrink-0" />
            <div className="flex-1 space-y-4">
              <div className="h-6 bg-gray-200 rounded w-3/4 sm:w-1/2" />
              <div className="h-4 bg-gray-200 rounded w-full sm:w-3/4" />
              <div className="h-4 bg-gray-200 rounded w-1/2 sm:w-1/4" />
            </div>
          </div>
        </div>
      ) : podcast ? (
        <div className="bg-white rounded-lg border border-gray-200 p-4 sm:p-6">
          <div className="flex flex-col sm:flex-row gap-4 sm:gap-6">
            {/* Podcast artwork */}
            {podcast.image_url ? (
              <img
                src={podcast.image_url}
                alt={`${podcast.title} artwork`}
                width={96}
                height={96}
                loading="eager"
                className="w-20 h-20 sm:w-24 sm:h-24 rounded-lg object-cover flex-shrink-0 mx-auto sm:mx-0 aspect-square"
              />
            ) : (
              <div className="w-20 h-20 sm:w-24 sm:h-24 bg-gradient-to-br from-primary-100 to-secondary-100 rounded-lg flex items-center justify-center flex-shrink-0 mx-auto sm:mx-0 aspect-square">
                <svg className="w-10 h-10 sm:w-12 sm:h-12 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
              </div>
            )}

            <div className="flex-1 text-center sm:text-left">
              {/* Title row with Unfollow button aligned right */}
              <div className="flex items-start justify-between gap-4">
                <h1 className="text-xl sm:text-2xl font-bold text-gray-900">{podcast.title}</h1>
                <Button
                  variant="secondary"
                  icon={<MinusIcon />}
                  iconOnlyMobile
                  isLoading={isUnfollowing}
                  onClick={() => {
                    if (isUnfollowing) return
                    setIsUnfollowing(true)
                    unfollow(podcastSlug!, {
                      onSuccess: () => {
                        showToast(`Unfollowed ${podcast.title}`, 'success')
                        navigate('/podcasts')
                      },
                      onError: (error) => {
                        showToast(`Failed to unfollow: ${error.message}`, 'error')
                        setIsUnfollowing(false)
                      },
                    })
                  }}
                >
                  Unfollow
                </Button>
              </div>
              {podcast.description ? (
                <div className="mt-2">
                  <ExpandableDescription html={podcast.description} maxLines={3} />
                </div>
              ) : (
                <p className="text-gray-600 mt-2">No description</p>
              )}

              <div className="flex flex-wrap items-center justify-center sm:justify-start gap-4 sm:gap-6 mt-4 text-sm">
                <div className="flex items-center gap-1 text-gray-600">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                  </svg>
                  <span>{podcast.episodes_count} episodes</span>
                </div>
                <div className="flex items-center gap-1 text-green-600">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>{podcast.episodes_processed} processed</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Episodes */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">
          Episodes
          {totalEpisodes > 0 && ` (${totalEpisodes})`}
        </h2>

        {episodesLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="animate-pulse bg-white rounded-lg border border-gray-200 p-4">
                <div className="flex gap-4">
                  <div className="w-10 h-10 bg-gray-200 rounded-full" />
                  <div className="flex-1 space-y-3">
                    <div className="h-4 bg-gray-200 rounded w-3/4" />
                    <div className="h-3 bg-gray-200 rounded w-1/4" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : allEpisodes.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <p className="text-gray-500">No episodes found</p>
          </div>
        ) : (
          <div className="space-y-3">
            {allEpisodes.map((episode, index) => (
              <EpisodeCard
                key={episode.external_id || index}
                episode={episode}
                podcastImageUrl={podcast?.image_url}
              />
            ))}

            {/* Load more trigger */}
            <div ref={loadMoreRef} className="py-4">
              {isFetchingNextPage && (
                <div className="flex justify-center">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
                </div>
              )}
              {!hasNextPage && allEpisodes.length > 0 && (
                <p className="text-center text-gray-400 text-sm">No more episodes</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
