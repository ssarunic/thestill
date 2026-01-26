import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useDigest, useDigestContent, useDigestEpisodes, useDeleteDigest } from '../hooks/useApi'
import type { DigestStatus, DigestEpisodeInfo } from '../api/types'
import ReactMarkdown from 'react-markdown'

// Status colors for badges
const statusColors: Record<DigestStatus, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  in_progress: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  partial: 'bg-orange-100 text-orange-700',
  failed: 'bg-red-100 text-red-700',
}

// Status labels
const statusLabels: Record<DigestStatus, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatShortDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`
}

// Episode state colors
const stateColors: Record<string, string> = {
  discovered: 'bg-gray-100 text-gray-600',
  downloaded: 'bg-blue-100 text-blue-700',
  downsampled: 'bg-indigo-100 text-indigo-700',
  transcribed: 'bg-purple-100 text-purple-700',
  cleaned: 'bg-amber-100 text-amber-700',
  summarized: 'bg-green-100 text-green-700',
}

type Tab = 'content' | 'episodes'

interface EpisodeItemProps {
  episode: DigestEpisodeInfo
}

function EpisodeItem({ episode }: EpisodeItemProps) {
  return (
    <div className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors">
      {/* Episode artwork */}
      {episode.image_url ? (
        <img
          src={episode.image_url}
          alt=""
          className="w-12 h-12 rounded-lg object-cover flex-shrink-0"
        />
      ) : (
        <div className="w-12 h-12 bg-gradient-to-br from-primary-100 to-secondary-100 rounded-lg flex items-center justify-center flex-shrink-0">
          <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
      )}

      <div className="flex-1 min-w-0">
        <Link
          to={`/podcasts/${episode.podcast_slug}/episodes/${episode.episode_slug}`}
          className="font-medium text-gray-900 hover:text-primary-600 line-clamp-1"
        >
          {episode.episode_title}
        </Link>
        <p className="text-sm text-gray-500 line-clamp-1">{episode.podcast_title}</p>
        <div className="flex items-center gap-2 mt-1">
          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${stateColors[episode.state]}`}>
            {episode.state}
          </span>
          {episode.pub_date && (
            <span className="text-xs text-gray-400">
              {formatShortDate(episode.pub_date)}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export default function DigestDetail() {
  const { digestId } = useParams<{ digestId: string }>()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<Tab>('content')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)

  const { data: digestData, isLoading: digestLoading, error: digestError } = useDigest(digestId || null)
  const { data: contentData, isLoading: contentLoading } = useDigestContent(digestId || null)
  const { data: episodesData, isLoading: episodesLoading } = useDigestEpisodes(digestId || null)
  const deleteMutation = useDeleteDigest()

  const handleDelete = async () => {
    if (digestId) {
      await deleteMutation.mutateAsync(digestId)
      navigate('/digests')
    }
  }

  if (digestError) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading digest</h2>
          <p className="text-red-600 text-sm">{digestError.message}</p>
          <Link to="/digests" className="mt-4 inline-block text-primary-600 hover:underline">
            &larr; Back to digests
          </Link>
        </div>
      </div>
    )
  }

  const digest = digestData?.digest

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Breadcrumb */}
      <nav className="text-sm flex items-center gap-1">
        <Link to="/digests" className="text-gray-500 hover:text-gray-700">Digests</Link>
        <span className="text-gray-400">/</span>
        <span className="text-gray-900 truncate">
          {digestLoading ? '...' : digest ? formatShortDate(digest.created_at) : 'Digest'}
        </span>
      </nav>

      {/* Header */}
      {digestLoading ? (
        <div className="animate-pulse bg-white rounded-lg border border-gray-200 p-6 space-y-4">
          <div className="h-8 bg-gray-200 rounded w-1/2" />
          <div className="h-5 bg-gray-200 rounded w-1/3" />
          <div className="flex gap-2">
            <div className="h-6 bg-gray-200 rounded w-20" />
            <div className="h-6 bg-gray-200 rounded w-32" />
          </div>
        </div>
      ) : digest ? (
        <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">
                Digest from {formatDate(digest.created_at)}
              </h1>
              <p className="text-gray-600 mt-1">
                Covers: {formatShortDate(digest.period_start)} - {formatShortDate(digest.period_end)}
              </p>
            </div>
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${statusColors[digest.status]}`}>
              {statusLabels[digest.status]}
            </span>
          </div>

          {/* Stats */}
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <div>
              <span className="text-gray-500">Episodes:</span>{' '}
              <span className="font-medium text-gray-900">
                {digest.episodes_completed}/{digest.episodes_total}
              </span>
              {digest.episodes_failed > 0 && (
                <span className="text-red-600 ml-1">({digest.episodes_failed} failed)</span>
              )}
            </div>
            {digest.success_rate > 0 && (
              <div>
                <span className="text-gray-500">Success rate:</span>{' '}
                <span className="font-medium text-gray-900">{Math.round(digest.success_rate)}%</span>
              </div>
            )}
            {digest.processing_time_seconds && (
              <div>
                <span className="text-gray-500">Processing time:</span>{' '}
                <span className="font-medium text-gray-900">
                  {formatDuration(digest.processing_time_seconds)}
                </span>
              </div>
            )}
          </div>

          {/* Error message */}
          {digest.error_message && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm text-red-700">{digest.error_message}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2 border-t border-gray-100">
            {showDeleteConfirm ? (
              <>
                <span className="text-sm text-gray-600">Delete this digest?</span>
                <button
                  onClick={handleDelete}
                  disabled={deleteMutation.isPending}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors"
                >
                  {deleteMutation.isPending ? 'Deleting...' : 'Yes, delete'}
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="px-3 py-1.5 text-sm font-medium rounded-lg text-red-600 hover:bg-red-50 transition-colors"
              >
                Delete digest
              </button>
            )}
          </div>
        </div>
      ) : null}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-4">
          <button
            onClick={() => setActiveTab('content')}
            className={`
              pb-3 px-1 text-sm font-medium border-b-2 transition-colors
              ${activeTab === 'content'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
              }
            `}
          >
            Content
          </button>
          <button
            onClick={() => setActiveTab('episodes')}
            className={`
              pb-3 px-1 text-sm font-medium border-b-2 transition-colors
              ${activeTab === 'episodes'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
              }
            `}
          >
            Episodes ({episodesData?.count || 0})
          </button>
        </nav>
      </div>

      {/* Tab content */}
      {activeTab === 'content' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          {contentLoading ? (
            <div className="animate-pulse space-y-4">
              <div className="h-6 bg-gray-200 rounded w-1/3" />
              <div className="h-4 bg-gray-200 rounded w-full" />
              <div className="h-4 bg-gray-200 rounded w-5/6" />
              <div className="h-4 bg-gray-200 rounded w-4/5" />
            </div>
          ) : contentData?.available && contentData.content ? (
            <article className="prose prose-gray max-w-none prose-headings:scroll-mt-4 prose-a:text-primary-600 prose-a:no-underline hover:prose-a:underline">
              <ReactMarkdown>{contentData.content}</ReactMarkdown>
            </article>
          ) : (
            <div className="text-center py-12">
              <div className="text-gray-400 mb-2">
                <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <p className="text-gray-500">Digest content not available</p>
              {digest?.status === 'pending' && (
                <p className="text-sm text-gray-400 mt-1">
                  This digest is still being processed
                </p>
              )}
              {digest?.status === 'failed' && (
                <p className="text-sm text-gray-400 mt-1">
                  Digest generation failed
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === 'episodes' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          {episodesLoading ? (
            <div className="animate-pulse space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg">
                  <div className="w-12 h-12 bg-gray-200 rounded-lg" />
                  <div className="flex-1 space-y-2">
                    <div className="h-5 bg-gray-200 rounded w-3/4" />
                    <div className="h-4 bg-gray-200 rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : episodesData && episodesData.episodes.length > 0 ? (
            <div className="space-y-2">
              {episodesData.episodes.map((episode) => (
                <EpisodeItem key={episode.episode_id} episode={episode} />
              ))}
            </div>
          ) : (
            <div className="text-center py-12">
              <p className="text-gray-500">No episodes in this digest</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
