import { useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useEpisode, useEpisodeTranscript, useEpisodeSummary } from '../hooks/useApi'
import TranscriptViewer from '../components/TranscriptViewer'
import SummaryViewer from '../components/SummaryViewer'
import AudioPlayer from '../components/AudioPlayer'
import ExpandableDescription from '../components/ExpandableDescription'
import PipelineActionButton from '../components/PipelineActionButton'
import type { PipelineStage } from '../api/types'

type Tab = 'transcript' | 'summary'

const stateColors: Record<string, string> = {
  discovered: 'bg-gray-100 text-gray-600',
  downloaded: 'bg-blue-100 text-blue-700',
  downsampled: 'bg-indigo-100 text-indigo-700',
  transcribed: 'bg-purple-100 text-purple-700',
  cleaned: 'bg-amber-100 text-amber-700',
  summarized: 'bg-green-100 text-green-700',
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default function EpisodeDetail() {
  const { podcastSlug, episodeSlug } = useParams<{ podcastSlug: string; episodeSlug: string }>()
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const queryClient = useQueryClient()

  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(podcastSlug!, episodeSlug!)
  const { data: transcriptData, isLoading: transcriptLoading } = useEpisodeTranscript(podcastSlug!, episodeSlug!)
  const { data: summaryData, isLoading: summaryLoading } = useEpisodeSummary(podcastSlug!, episodeSlug!)

  // Handle task completion - refresh relevant data
  const handleTaskComplete = useCallback((stage: PipelineStage) => {
    // Always refresh episode data to get updated state
    queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug] })

    // Refresh transcript after clean stage completes
    if (stage === 'clean') {
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug, 'transcript'] })
    }

    // Refresh summary after summarize stage completes
    if (stage === 'summarize') {
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug, 'summary'] })
    }
  }, [queryClient, podcastSlug, episodeSlug])

  if (episodeError) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading episode</h2>
          <p className="text-red-600 text-sm">{episodeError.message}</p>
          <Link to="/podcasts" className="mt-4 inline-block text-primary-600 hover:underline">
            ← Back to podcasts
          </Link>
        </div>
      </div>
    )
  }

  const episode = episodeData?.episode

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <nav className="text-sm flex flex-wrap items-center gap-1">
        <Link to="/podcasts" className="text-gray-500 hover:text-gray-700">Podcasts</Link>
        <span className="text-gray-400">/</span>
        <Link to={`/podcasts/${podcastSlug}`} className="text-gray-500 hover:text-gray-700 truncate max-w-[120px] sm:max-w-none">{episodeLoading ? '...' : episode?.podcast_title}</Link>
        <span className="text-gray-400 hidden sm:inline">/</span>
        <span className="text-gray-900 truncate max-w-[150px] sm:max-w-none hidden sm:inline">{episodeLoading ? '...' : episode?.title}</span>
      </nav>

      {/* Header */}
      {episodeLoading ? (
        <div className="animate-pulse bg-white rounded-lg border border-gray-200 p-4 sm:p-6 space-y-4">
          <div className="h-6 bg-gray-200 rounded w-full sm:w-3/4" />
          <div className="h-4 bg-gray-200 rounded w-2/3 sm:w-1/2" />
          <div className="h-10 bg-gray-200 rounded" />
        </div>
      ) : episode ? (
        <div className="bg-white rounded-lg border border-gray-200 p-4 sm:p-6 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-start gap-4">
            {/* Episode/Podcast artwork - prioritize episode artwork, fall back to podcast artwork */}
            {(episode.image_url || episode.podcast_image_url) ? (
              <img
                src={episode.image_url || episode.podcast_image_url || ''}
                alt={`${episode.title} artwork`}
                className="w-20 h-20 sm:w-24 sm:h-24 rounded-lg object-cover flex-shrink-0 mx-auto sm:mx-0"
              />
            ) : (
              <div className="w-20 h-20 sm:w-24 sm:h-24 bg-gradient-to-br from-primary-100 to-secondary-100 rounded-lg flex items-center justify-center flex-shrink-0 mx-auto sm:mx-0">
                <svg className="w-8 h-8 sm:w-10 sm:h-10 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
              </div>
            )}
            <div className="flex-1 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 sm:gap-4 text-center sm:text-left">
              <div>
                <h1 className="text-xl sm:text-2xl font-bold text-gray-900">{episode.title}</h1>
                <p className="text-gray-600 mt-1">{episode.podcast_title}</p>
              </div>
              <span className={`px-3 py-1 rounded-full text-sm font-medium self-center sm:self-start ${stateColors[episode.state]}`}>
                {episode.state === 'summarized' ? 'Ready' : episode.state.charAt(0).toUpperCase() + episode.state.slice(1)}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:gap-4 text-sm text-gray-500">
            <span>{formatDate(episode.pub_date)}</span>
            {episode.duration_formatted && (
              <>
                <span className="hidden sm:inline">•</span>
                <span>{episode.duration_formatted}</span>
              </>
            )}
          </div>

          {/* Pipeline Action Button */}
          <div className="border-t border-gray-100 pt-4">
            <PipelineActionButton
              podcastSlug={podcastSlug!}
              episodeSlug={episodeSlug!}
              episodeId={episode.id}
              episodeState={episode.state}
              onTaskComplete={handleTaskComplete}
            />
          </div>

          {/* Audio Player */}
          <div className="border-t border-gray-100 pt-4">
            <AudioPlayer audioUrl={episode.audio_url} title={episode.title} />
          </div>

          {episode.description && (
            <div className="border-t border-gray-100 pt-4">
              <ExpandableDescription html={episode.description} maxLines={3} />
            </div>
          )}
        </div>
      ) : null}

      {/* Content Tabs */}
      <div className="bg-white rounded-lg border border-gray-200">
        {/* Tab Headers */}
        <div className="border-b border-gray-200">
          <nav className="flex">
            <button
              onClick={() => setActiveTab('summary')}
              className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === 'summary'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              Summary
              {episode?.has_summary && (
                <span className="ml-2 w-2 h-2 inline-block rounded-full bg-green-400" />
              )}
            </button>
            <button
              onClick={() => setActiveTab('transcript')}
              className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === 'transcript'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              Transcript
              {episode?.has_transcript && (
                <span className="ml-2 w-2 h-2 inline-block rounded-full bg-green-400" />
              )}
            </button>
          </nav>
        </div>

        {/* Tab Content */}
        <div className="p-4 sm:p-6">
          {activeTab === 'summary' ? (
            <SummaryViewer
              content={summaryData?.content ?? ''}
              isLoading={summaryLoading}
              available={summaryData?.available}
              episodeState={episode?.state}
            />
          ) : (
            <TranscriptViewer
              content={transcriptData?.content ?? ''}
              isLoading={transcriptLoading}
              available={transcriptData?.available}
              episodeState={episode?.state}
            />
          )}
        </div>
      </div>
    </div>
  )
}
