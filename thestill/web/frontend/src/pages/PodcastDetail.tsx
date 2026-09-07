import { useParams, Link, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  usePodcast,
  usePodcastEpisodesInfinite,
  useFollowPodcast,
  useUnfollowPodcast,
  useProcessingStageByEpisodeId,
  useInvalidateEpisodesWhenRefreshSettles,
} from '../hooks/useApi'
import { useToast } from '../components/Toast'
import EpisodeCard from '../components/EpisodeCard'
import ExpandableDescription from '../components/ExpandableDescription'
import { ExternalLink } from '../components/ExternalLink'
import Button, { ExternalLinkIcon, MinusIcon, PlusIcon } from '../components/Button'
import { buttonClassName } from '../components/buttonStyles'
import PageHero from '../components/PageHero'
import ActionRow from '../components/ActionRow'
import Artwork from '../components/Artwork'
import { artworkFrameClass } from '../components/artworkRoles'
import MetaEyebrow from '../components/MetaEyebrow'
import DefinitionList from '../components/DefinitionList'
import Panel from '../components/Panel'
import { hostOf } from '../utils/episodeFormat'

export default function PodcastDetail() {
  const { podcastSlug } = useParams<{ podcastSlug: string }>()
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [isMutating, setIsMutating] = useState(false)
  const { data: podcastData, isLoading: podcastLoading, error: podcastError } = usePodcast(podcastSlug!)
  // Spec #74 — refetch the episode list once the open-triggered refresh lands.
  useInvalidateEpisodesWhenRefreshSettles(podcastSlug!, podcastData?.podcast?.refresh_pending)
  const { mutate: follow } = useFollowPodcast()
  const { mutate: unfollow } = useUnfollowPodcast()
  const {
    data: episodesData,
    isLoading: episodesLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = usePodcastEpisodesInfinite(podcastSlug!)

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

  const processingByEpisodeId = useProcessingStageByEpisodeId()

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
        <Link to="/podcasts" className="text-muted hover:text-gray-700">Podcasts</Link>
        <span className="mx-2 text-gray-400">/</span>
        <span className="text-ink">{podcastLoading ? '...' : podcast?.title}</span>
      </nav>

      {/* Header — spec #76 §5 primitives: hero, action row, description. */}
      {podcastLoading ? (
        <div className="animate-pulse" aria-hidden="true">
          <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
            <div className="flex justify-center sm:block">
              <div className={`${artworkFrameClass('card')} bg-gray-200`} />
            </div>
            <div className="flex-1 space-y-3">
              <div className="h-4 w-1/3 rounded bg-gray-200" />
              <div className="h-7 w-3/4 rounded bg-gray-200 sm:w-1/2" />
              <div className="h-5 w-1/3 rounded bg-gray-200" />
              <div className="h-12 w-32 rounded-full bg-gray-200" />
            </div>
          </div>
        </div>
      ) : podcast ? (
        <PageHero
          artwork={<Artwork role="card" sources={[podcast.image_url]} alt={`${podcast.title} artwork`} loading="eager" />}
          eyebrow={
            <MetaEyebrow
              items={[
                podcast.primary_category,
                podcast.primary_subcategory,
                podcast.show_type === 'serial' ? 'Serial' : null,
                podcast.explicit ? 'Explicit' : null,
              ]}
            />
          }
          title={podcast.title}
          identity={podcast.author ? <p className="text-base text-gray-700">By {podcast.author}</p> : undefined}
        >
          <ActionRow
            primary={
              podcast.is_following ? (
                <Button
                  variant="secondary"
                  size="lg"
                  pill
                  icon={<MinusIcon />}
                  isLoading={isMutating}
                  onClick={() => {
                    if (isMutating) return
                    setIsMutating(true)
                    unfollow(podcastSlug!, {
                      onSuccess: () => {
                        showToast(`Unfollowed ${podcast.title}`, 'success')
                        navigate('/podcasts')
                      },
                      onError: (error) => {
                        showToast(`Failed to unfollow: ${error.message}`, 'error')
                        setIsMutating(false)
                      },
                    })
                  }}
                >
                  Unfollow
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="lg"
                  pill
                  icon={<PlusIcon />}
                  isLoading={isMutating}
                  onClick={() => {
                    if (isMutating) return
                    setIsMutating(true)
                    follow(podcastSlug!, {
                      onSuccess: () => {
                        showToast(`Followed ${podcast.title}`, 'success')
                        setIsMutating(false)
                      },
                      onError: (error) => {
                        showToast(`Failed to follow: ${error.message}`, 'error')
                        setIsMutating(false)
                      },
                    })
                  }}
                >
                  Follow
                </Button>
              )
            }
            actions={
              podcast.website_url ? (
                <a
                  href={podcast.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Website"
                  className={buttonClassName({ variant: 'secondary', size: 'icon' })}
                >
                  <span className="h-5 w-5">
                    <ExternalLinkIcon />
                  </span>
                  <span className="sr-only">Website</span>
                </a>
              ) : null
            }
          />
          {podcast.description ? (
            <ExpandableDescription html={podcast.description} maxLines={3} />
          ) : (
            <p className="text-gray-600">No description</p>
          )}
        </PageHero>
      ) : null}

      {/* Episodes */}
      <div>
        <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1">
          <h2 className="text-section text-ink">
            Episodes
            {totalEpisodes > 0 && ` (${totalEpisodes})`}
          </h2>
          {/* Spec #74 — an open enqueued a feed refresh; the podcast query
              polls until it settles and the episode list is invalidated
              then. */}
          {podcast?.refresh_pending && (
            <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
              <span
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-primary-600 border-t-transparent"
                aria-hidden="true"
              />
              {podcast.episodes_count === 0 ? 'Loading episodes…' : 'Checking for new episodes…'}
            </span>
          )}
        </div>

        {!episodesData ||
        episodesLoading ||
        (allEpisodes.length === 0 &&
          (podcast?.last_processed === null || (podcast?.episodes_count ?? 0) > 0)) ? (
          // Skeleton during the first fetch (``!episodesData`` covers the
          // brief render gap before TanStack Query starts fetching). Also
          // keep the skeleton up in two race-window cases:
          //   1. ``last_processed`` null  — the background refresh is still
          //      discovering episodes (lazy-import flow).
          //   2. ``episodes_count > 0`` but the list came back empty — the
          //      /episodes handler was waiting on a SQLite write lock and
          //      saw a stale snapshot. The next 5s refetch will land the
          //      real list.
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="animate-pulse bg-surface rounded-lg border border-hairline p-4">
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
          <div className="text-center py-12 bg-surface rounded-lg border border-hairline">
            <p className="text-muted">No episodes found</p>
          </div>
        ) : (
          <div className="space-y-3">
            {allEpisodes.map((episode, index) => (
              <EpisodeCard
                key={episode.external_id || index}
                episode={episode}
                podcastImageUrl={podcast?.image_url}
                processingStage={processingByEpisodeId.get(episode.id)}
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

      {/* Spec #76 §5.4 — every remaining show fact in one labelled place. */}
      {podcast && (
        <Panel className="px-4 py-3 sm:px-6 sm:py-4">
          <DefinitionList
            heading="Details"
            rows={[
              { label: 'Author', value: podcast.author },
              {
                label: 'Category',
                value: podcast.primary_category
                  ? podcast.primary_subcategory
                    ? `${podcast.primary_category} › ${podcast.primary_subcategory}`
                    : podcast.primary_category
                  : null,
              },
              { label: 'Episodes', value: podcast.episodes_count, numeric: true },
              { label: 'Processed', value: podcast.episodes_processed, numeric: true },
              { label: 'Status', value: podcast.is_complete ? 'Complete series · No new episodes' : null },
              { label: 'Explicit', value: podcast.explicit == null ? null : podcast.explicit ? 'Yes' : 'No' },
              {
                label: 'Website',
                value:
                  podcast.website_url && hostOf(podcast.website_url) ? (
                    <ExternalLink href={podcast.website_url} className="text-sm">
                      {hostOf(podcast.website_url)}
                    </ExternalLink>
                  ) : null,
              },
              { label: 'Copyright', value: podcast.copyright },
            ]}
          />
        </Panel>
      )}
    </div>
  )
}
