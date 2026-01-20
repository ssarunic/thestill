import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import type { Episode, EpisodeWithPodcast, FailureType } from '../api/types'
import { useRetryFailedEpisode } from '../hooks/useApi'
import FailureDetailsModal from './FailureDetailsModal'
import EpisodePreviewTooltip from './EpisodePreviewTooltip'

interface EpisodeCardProps {
  episode: Episode | EpisodeWithPodcast
  podcastTitle?: string  // Optional podcast title for modal display
  showPodcastName?: boolean  // Show podcast name below title (default: false)
  // Selection props (optional - for use in Episodes browser)
  isSelected?: boolean
  onSelect?: (episodeId: string, selected: boolean) => void
  // Artwork fallback (optional - use podcast image if episode has none)
  podcastImageUrl?: string | null
}

const stateColors: Record<string, string> = {
  discovered: 'bg-gray-100 text-gray-600',
  downloaded: 'bg-blue-100 text-blue-700',
  downsampled: 'bg-indigo-100 text-indigo-700',
  transcribed: 'bg-purple-100 text-purple-700',
  cleaned: 'bg-amber-100 text-amber-700',
  summarized: 'bg-green-100 text-green-700',
}

const stateLabels: Record<string, string> = {
  discovered: 'Discovered',
  downloaded: 'Downloaded',
  downsampled: 'Downsampled',
  transcribed: 'Transcribed',
  cleaned: 'Cleaned',
  summarized: 'Ready',
}

const failureTypeColors: Record<FailureType, string> = {
  fatal: 'bg-red-100 text-red-700 border-red-200',
  transient: 'bg-yellow-100 text-yellow-700 border-yellow-200',
}

const failureTypeLabels: Record<FailureType, string> = {
  fatal: 'Failed',
  transient: 'Retrying',
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}


export default function EpisodeCard({
  episode,
  podcastTitle,
  showPodcastName = false,
  isSelected,
  onSelect,
  podcastImageUrl,
}: EpisodeCardProps) {
  const [showFailureModal, setShowFailureModal] = useState(false)
  const [showTooltip, setShowTooltip] = useState(false)
  const tooltipTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryMutation = useRetryFailedEpisode()

  const isProcessed = episode.state === 'cleaned' || episode.state === 'summarized'
  const isFailed = episode.is_failed && episode.failure_type
  const isSelectable = onSelect !== undefined

  // Get artwork URL - prioritize episode image, fall back to podcast image
  const episodeWithPodcast = episode as EpisodeWithPodcast
  const artworkUrl = episode.image_url || episodeWithPodcast.podcast_image_url || podcastImageUrl || null

  // Get podcast title for display
  const displayPodcastTitle = podcastTitle || episodeWithPodcast.podcast_title

  const handleMouseEnter = () => {
    if (!isProcessed) return
    const timeout = setTimeout(() => {
      setShowTooltip(true)
    }, 500)
    tooltipTimeoutRef.current = timeout
  }

  const handleMouseLeave = () => {
    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current)
      tooltipTimeoutRef.current = null
    }
    setShowTooltip(false)
  }

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation()
    onSelect?.(episode.id, e.target.checked)
  }

  const handleRetry = async () => {
    await retryMutation.mutateAsync(episode.id)
    setShowFailureModal(false)
  }

  // Determine card border style based on failure and selection state
  const cardBorderClass = isSelected
    ? 'border-indigo-500 ring-2 ring-indigo-200'
    : isFailed
      ? episode.failure_type === 'fatal'
        ? 'border-red-300 hover:border-red-400'
        : 'border-yellow-300 hover:border-yellow-400'
      : 'border-gray-200 hover:border-gray-300 hover:shadow-sm'

  const content = (
    <div className="flex items-start gap-3 sm:gap-4">
      {/* Episode artwork */}
      {artworkUrl ? (
        <img
          src={artworkUrl}
          alt=""
          className="w-10 h-10 rounded-md object-cover flex-shrink-0"
        />
      ) : (
        <div className={`w-10 h-10 rounded-md flex items-center justify-center flex-shrink-0 ${
          isFailed
            ? episode.failure_type === 'fatal'
              ? 'bg-red-100'
              : 'bg-yellow-100'
            : 'bg-gray-100'
        }`}>
          <svg className={`w-5 h-5 ${
            isFailed
              ? episode.failure_type === 'fatal'
                ? 'text-red-400'
                : 'text-yellow-400'
              : 'text-gray-400'
          }`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
      )}

      <div className="flex-1 min-w-0">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 sm:gap-2">
          <div className="min-w-0">
            <h3 className="font-medium text-gray-900 line-clamp-2 text-sm sm:text-base">{episode.title}</h3>
            {showPodcastName && displayPodcastTitle && (
              <p className="text-xs text-gray-500 mt-0.5 truncate">{displayPodcastTitle}</p>
            )}
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0 self-start">
            {/* Show failure badge if failed */}
            {isFailed && episode.failure_type && (
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${failureTypeColors[episode.failure_type]}`}>
                {failureTypeLabels[episode.failure_type]}
              </span>
            )}
            {/* Always show state badge */}
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${stateColors[episode.state]}`}>
              {stateLabels[episode.state]}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 sm:gap-3 mt-2 text-xs sm:text-sm text-gray-500">
          <span>{formatDate(episode.pub_date)}</span>
          {episode.duration_formatted && (
            <>
              <span className="hidden sm:inline">•</span>
              <span>{episode.duration_formatted}</span>
            </>
          )}
        </div>

        {/* Failure info */}
        {isFailed && episode.failed_at_stage && (
          <div className={`mt-2 text-xs ${
            episode.failure_type === 'fatal' ? 'text-red-600' : 'text-yellow-600'
          }`}>
            <span className="font-medium">Failed at:</span> {episode.failed_at_stage}
            {episode.failure_reason && (
              <span className="ml-1 text-gray-500 truncate max-w-xs inline-block align-bottom">
                - {episode.failure_reason.length > 50
                    ? episode.failure_reason.slice(0, 50) + '...'
                    : episode.failure_reason}
              </span>
            )}
            <button
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                setShowFailureModal(true)
              }}
              className={`ml-2 underline hover:no-underline ${
                episode.failure_type === 'fatal' ? 'text-red-700' : 'text-yellow-700'
              }`}
            >
              View details
            </button>
          </div>
        )}

      </div>
    </div>
  )

  return (
    <>
      <div
        className={`relative p-3 sm:p-4 bg-white rounded-lg border transition-all ${cardBorderClass}`}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        <div className="flex items-start gap-3 sm:gap-4">
          {/* Checkbox (only when selectable) - outside Link to prevent navigation on click */}
          {isSelectable && (
            <div className="flex-shrink-0 pt-1">
              <input
                type="checkbox"
                checked={isSelected}
                onChange={handleCheckboxChange}
                className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500 cursor-pointer"
              />
            </div>
          )}

          <Link
            to={`/podcasts/${episode.podcast_slug}/episodes/${episode.slug}`}
            className="block flex-1 min-w-0"
          >
            {content}
          </Link>
        </div>

        {/* Hover tooltip */}
        {showTooltip && isProcessed && (
          <EpisodePreviewTooltip
            podcastSlug={episode.podcast_slug}
            episodeSlug={episode.slug}
          />
        )}
      </div>

      {/* Failure details modal */}
      {isFailed && episode.failure_type && episode.failed_at_stage && (
        <FailureDetailsModal
          isOpen={showFailureModal}
          onClose={() => setShowFailureModal(false)}
          episodeTitle={episode.title}
          episodeSlug={episode.slug}
          podcastSlug={episode.podcast_slug}
          podcastTitle={podcastTitle}
          failedAtStage={episode.failed_at_stage}
          failureReason={episode.failure_reason ?? null}
          failureType={episode.failure_type}
          failedAt={episode.failed_at ?? null}
          onRetry={handleRetry}
          isRetrying={retryMutation.isPending}
        />
      )}
    </>
  )
}
