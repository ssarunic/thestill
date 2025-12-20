import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useEpisode, useEpisodeTranscript, useEpisodeSummary } from '../hooks/useApi'
import TranscriptViewer from '../components/TranscriptViewer'
import SummaryViewer from '../components/SummaryViewer'
import AudioPlayer from '../components/AudioPlayer'
import ExpandableDescription from '../components/ExpandableDescription'

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
  const { episodeId } = useParams<{ episodeId: string }>()
  const [activeTab, setActiveTab] = useState<Tab>('summary')

  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(episodeId!)
  const { data: transcriptData, isLoading: transcriptLoading } = useEpisodeTranscript(episodeId!)
  const { data: summaryData, isLoading: summaryLoading } = useEpisodeSummary(episodeId!)

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
      <nav className="text-sm">
        <Link to="/podcasts" className="text-gray-500 hover:text-gray-700">Podcasts</Link>
        <span className="mx-2 text-gray-400">/</span>
        <span className="text-gray-500">{episodeLoading ? '...' : episode?.podcast_title}</span>
        <span className="mx-2 text-gray-400">/</span>
        <span className="text-gray-900 truncate">{episodeLoading ? '...' : episode?.title}</span>
      </nav>

      {/* Header */}
      {episodeLoading ? (
        <div className="animate-pulse bg-white rounded-lg border border-gray-200 p-6 space-y-4">
          <div className="h-6 bg-gray-200 rounded w-3/4" />
          <div className="h-4 bg-gray-200 rounded w-1/2" />
          <div className="h-10 bg-gray-200 rounded" />
        </div>
      ) : episode ? (
        <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{episode.title}</h1>
              <p className="text-gray-600 mt-1">{episode.podcast_title}</p>
            </div>
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${stateColors[episode.state]}`}>
              {episode.state === 'summarized' ? 'Ready' : episode.state.charAt(0).toUpperCase() + episode.state.slice(1)}
            </span>
          </div>

          <div className="flex items-center gap-4 text-sm text-gray-500">
            <span>{formatDate(episode.pub_date)}</span>
            {episode.duration && (
              <>
                <span>•</span>
                <span>{episode.duration}</span>
              </>
            )}
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
              className={`px-6 py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
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
              className={`px-6 py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
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
        <div className="p-6">
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
