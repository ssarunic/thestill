import { Link } from 'react-router-dom'
import type { PodcastSummary } from '../api/types'

interface PodcastCardProps {
  podcast: PodcastSummary
}

export default function PodcastCard({ podcast }: PodcastCardProps) {
  const progressPercent = podcast.episodes_count > 0
    ? Math.round((podcast.episodes_processed / podcast.episodes_count) * 100)
    : 0

  return (
    <Link
      to={`/podcasts/${podcast.index}`}
      className="block bg-white rounded-lg border border-gray-200 hover:border-gray-300 hover:shadow-md transition-all overflow-hidden"
    >
      <div className="p-6">
        {/* Header */}
        <div className="flex items-start gap-4">
          {/* Placeholder artwork */}
          <div className="w-16 h-16 bg-gradient-to-br from-primary-100 to-secondary-100 rounded-lg flex items-center justify-center flex-shrink-0">
            <svg className="w-8 h-8 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-gray-900 truncate">{podcast.title}</h3>
            <p className="text-sm text-gray-500 mt-1 line-clamp-2">{podcast.description || 'No description'}</p>
          </div>
        </div>

        {/* Stats */}
        <div className="mt-4 flex items-center gap-4 text-sm">
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

        {/* Progress bar */}
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
            <span>Processing progress</span>
            <span>{progressPercent}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-green-500 rounded-full transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      </div>
    </Link>
  )
}
