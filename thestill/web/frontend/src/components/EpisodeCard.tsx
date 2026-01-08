import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Episode, FailureType } from '../api/types'
import { useRetryFailedEpisode } from '../hooks/useApi'
import FailureDetailsModal from './FailureDetailsModal'

interface EpisodeCardProps {
  episode: Episode
  podcastTitle?: string  // Optional podcast title for modal display
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


export default function EpisodeCard({ episode, podcastTitle }: EpisodeCardProps) {
  const [showFailureModal, setShowFailureModal] = useState(false)
  const retryMutation = useRetryFailedEpisode()

  const isProcessed = episode.state === 'cleaned' || episode.state === 'summarized'
  const isFailed = episode.is_failed && episode.failure_type

  const handleRetry = async () => {
    await retryMutation.mutateAsync(episode.id)
    setShowFailureModal(false)
  }

  // Determine card border style based on failure state
  const cardBorderClass = isFailed
    ? episode.failure_type === 'fatal'
      ? 'border-red-300 hover:border-red-400'
      : 'border-yellow-300 hover:border-yellow-400'
    : 'border-gray-200 hover:border-gray-300'

  const content = (
    <div className="flex items-start gap-3 sm:gap-4">
      {/* Episode number */}
      <div className={`w-8 h-8 sm:w-10 sm:h-10 rounded-full flex items-center justify-center flex-shrink-0 ${
        isFailed
          ? episode.failure_type === 'fatal'
            ? 'bg-red-100'
            : 'bg-yellow-100'
          : 'bg-gray-100'
      }`}>
        <span className={`text-xs sm:text-sm font-medium ${
          isFailed
            ? episode.failure_type === 'fatal'
              ? 'text-red-600'
              : 'text-yellow-600'
            : 'text-gray-600'
        }`}>#{episode.episode_index}</span>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 sm:gap-2">
          <h3 className="font-medium text-gray-900 line-clamp-2 text-sm sm:text-base">{episode.title}</h3>
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

        {/* Content availability indicators */}
        {isProcessed && !isFailed && (
          <div className="flex items-center gap-3 mt-2">
            {episode.transcript_available && (
              <span className="text-xs text-green-600 flex items-center gap-1">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Transcript
              </span>
            )}
            {episode.summary_available && (
              <span className="text-xs text-green-600 flex items-center gap-1">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Summary
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )

  return (
    <>
      <Link
        to={`/podcasts/${episode.podcast_slug}/episodes/${episode.slug}`}
        className={`block p-3 sm:p-4 bg-white rounded-lg border hover:shadow-sm transition-all ${cardBorderClass}`}
      >
        {content}
      </Link>

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
