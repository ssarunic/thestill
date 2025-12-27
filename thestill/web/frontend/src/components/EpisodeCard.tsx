import { Link } from 'react-router-dom'
import type { Episode } from '../api/types'

interface EpisodeCardProps {
  episode: Episode
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

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}


export default function EpisodeCard({ episode }: EpisodeCardProps) {
  const isProcessed = episode.state === 'cleaned' || episode.state === 'summarized'

  const content = (
    <div className="flex items-start gap-3 sm:gap-4">
      {/* Episode number */}
      <div className="w-8 h-8 sm:w-10 sm:h-10 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
        <span className="text-xs sm:text-sm font-medium text-gray-600">#{episode.episode_index}</span>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 sm:gap-2">
          <h3 className="font-medium text-gray-900 line-clamp-2 text-sm sm:text-base">{episode.title}</h3>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 self-start ${stateColors[episode.state]}`}>
            {stateLabels[episode.state]}
          </span>
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

        {/* Content availability indicators */}
        {isProcessed && (
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
    <Link
      to={`/podcasts/${episode.podcast_slug}/episodes/${episode.slug}`}
      className="block p-3 sm:p-4 bg-white rounded-lg border border-gray-200 hover:border-gray-300 hover:shadow-sm transition-all"
    >
      {content}
    </Link>
  )
}
