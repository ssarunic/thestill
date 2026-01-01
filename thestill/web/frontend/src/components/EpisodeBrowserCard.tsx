import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import type { EpisodeWithPodcast } from '../api/types'
import EpisodePreviewTooltip from './EpisodePreviewTooltip'

interface EpisodeBrowserCardProps {
  episode: EpisodeWithPodcast
  isSelected: boolean
  onSelect: (episodeId: string, selected: boolean) => void
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

export default function EpisodeBrowserCard({ episode, isSelected, onSelect }: EpisodeBrowserCardProps) {
  const [showTooltip, setShowTooltip] = useState(false)
  const tooltipTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isProcessed = episode.state === 'cleaned' || episode.state === 'summarized'

  const handleMouseEnter = () => {
    // Show tooltip after 500ms delay
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
    onSelect(episode.id, e.target.checked)
  }

  return (
    <div
      className={`relative p-3 sm:p-4 bg-white rounded-lg border transition-all ${
        isSelected
          ? 'border-indigo-500 ring-2 ring-indigo-200'
          : 'border-gray-200 hover:border-gray-300 hover:shadow-sm'
      }`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div className="flex items-start gap-3">
        {/* Checkbox */}
        <div className="flex-shrink-0 pt-1">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={handleCheckboxChange}
            className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500 cursor-pointer"
          />
        </div>

        {/* Podcast thumbnail */}
        {episode.podcast_image_url ? (
          <img
            src={episode.podcast_image_url}
            alt=""
            className="w-10 h-10 rounded-md object-cover flex-shrink-0"
          />
        ) : (
          <div className="w-10 h-10 bg-gray-100 rounded-md flex items-center justify-center flex-shrink-0">
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 sm:gap-2">
            <div className="min-w-0">
              <Link
                to={`/podcasts/${episode.podcast_slug}/episodes/${episode.slug}`}
                className="font-medium text-gray-900 line-clamp-2 text-sm sm:text-base hover:text-indigo-600"
              >
                {episode.title}
              </Link>
              <p className="text-xs text-gray-500 mt-0.5 truncate">
                {episode.podcast_title}
              </p>
            </div>
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 self-start ${
                stateColors[episode.state]
              }`}
            >
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
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                  Transcript
                </span>
              )}
              {episode.summary_available && (
                <span className="text-xs text-green-600 flex items-center gap-1">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                  Summary
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Hover tooltip */}
      {showTooltip && isProcessed && (
        <EpisodePreviewTooltip
          podcastSlug={episode.podcast_slug}
          episodeSlug={episode.slug}
        />
      )}
    </div>
  )
}
