import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useBriefings, useCreateBriefing, useDeleteBriefing, usePreviewBriefing } from '../hooks/useApi'
import type { Briefing, BriefingStatus, BriefingPreviewEpisode, CreateBriefingRequest } from '../api/types'

// Status colors for badges
const statusColors: Record<BriefingStatus, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  in_progress: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  partial: 'bg-orange-100 text-orange-700',
  failed: 'bg-red-100 text-red-700',
}

// Status labels
const statusLabels: Record<BriefingStatus, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`
}

interface BriefingCardProps {
  briefing: Briefing
  onDelete: (briefingId: string) => void
  isDeleting: boolean
}

function BriefingCard({ briefing, onDelete, isDeleting }: BriefingCardProps) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const isActive = briefing.status === 'pending' || briefing.status === 'in_progress'

  return (
    <div className={`bg-white rounded-lg border shadow-sm hover:shadow-md transition-shadow ${
      isActive ? 'border-blue-300 bg-blue-50/30' : 'border-gray-200'
    }`}>
      <div className="p-4">
        {/* Progress indicator for active briefings */}
        {isActive && (
          <div className="mb-3">
            <div className="flex items-center gap-2 text-sm text-blue-600 mb-2">
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                />
              </svg>
              <span>Processing episodes...</span>
            </div>
            <div className="w-full bg-blue-100 rounded-full h-1.5">
              <div
                className="bg-blue-600 h-1.5 rounded-full transition-all duration-300"
                style={{
                  width: briefing.episodes_total > 0
                    ? `${Math.round((briefing.episodes_completed / briefing.episodes_total) * 100)}%`
                    : '0%'
                }}
              />
            </div>
            <p className="text-xs text-blue-600 mt-1">
              {briefing.episodes_completed} of {briefing.episodes_total} episodes completed
            </p>
          </div>
        )}

        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            {/* Title and link */}
            <Link
              to={`/briefings/${briefing.id}`}
              className="font-medium text-gray-900 hover:text-primary-600"
            >
              Briefing from {formatDate(briefing.created_at)}
            </Link>

            {/* Period covered */}
            <p className="text-sm text-gray-500 mt-1">
              Covers: {formatDate(briefing.period_start)} - {formatDate(briefing.period_end)}
            </p>

            {/* Badges */}
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[briefing.status]}`}>
                {statusLabels[briefing.status]}
              </span>
              <span className="text-xs text-gray-500">
                {briefing.episodes_completed}/{briefing.episodes_total} episodes
              </span>
              {briefing.success_rate > 0 && briefing.success_rate < 100 && (
                <span className="text-xs text-gray-500">
                  ({Math.round(briefing.success_rate)}% success)
                </span>
              )}
              {briefing.processing_time_seconds && (
                <span className="text-xs text-gray-500">
                  {formatDuration(briefing.processing_time_seconds)}
                </span>
              )}
            </div>

            {/* Error message */}
            {briefing.error_message && (
              <p className="mt-2 text-sm text-red-600 line-clamp-1">
                {briefing.error_message}
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-2">
            <Link
              to={`/briefings/${briefing.id}`}
              className="px-3 py-1.5 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 transition-colors text-center"
            >
              View
            </Link>
            {showDeleteConfirm ? (
              <div className="flex gap-1">
                <button
                  onClick={() => onDelete(briefing.id)}
                  disabled={isDeleting}
                  className="px-2 py-1 text-xs font-medium rounded bg-red-600 text-white hover:bg-red-700"
                >
                  {isDeleting ? '...' : 'Yes'}
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="px-2 py-1 text-xs font-medium rounded bg-gray-200 text-gray-700 hover:bg-gray-300"
                >
                  No
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="px-3 py-1.5 text-sm font-medium rounded-lg text-gray-600 bg-gray-100 hover:bg-gray-200 transition-colors"
              >
                Delete
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

interface CreateBriefingModalProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (request: CreateBriefingRequest) => void
  isCreating: boolean
  preview: BriefingPreviewEpisode[] | null
  previewTotal: number | null
  onPreview: (request: CreateBriefingRequest) => void
  isLoadingPreview: boolean
}

function CreateBriefingModal({
  isOpen,
  onClose,
  onSubmit,
  isCreating,
  preview,
  previewTotal,
  onPreview,
  isLoadingPreview,
}: CreateBriefingModalProps) {
  const [sinceDays, setSinceDays] = useState(7)
  const [maxEpisodes, setMaxEpisodes] = useState(10)
  const [readyOnly, setReadyOnly] = useState(true)
  const [excludeBriefed, setExcludeBriefed] = useState(false)

  if (!isOpen) return null

  const handlePreview = () => {
    onPreview({
      since_days: sinceDays,
      max_episodes: maxEpisodes,
      ready_only: readyOnly,
      exclude_briefed: excludeBriefed,
    })
  }

  const handleSubmit = () => {
    onSubmit({
      since_days: sinceDays,
      max_episodes: maxEpisodes,
      ready_only: readyOnly,
      exclude_briefed: excludeBriefed,
    })
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-gray-200">
          <h2 className="text-xl font-semibold text-gray-900">Create New Briefing</h2>
          <p className="text-sm text-gray-500 mt-1">
            Generate a briefing from your processed podcast episodes
          </p>
        </div>

        <div className="p-6 space-y-4">
          {/* Time window */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Time window (days)
            </label>
            <input
              type="number"
              min={1}
              max={365}
              value={sinceDays}
              onChange={(e) => setSinceDays(parseInt(e.target.value) || 7)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              Include episodes from the last {sinceDays} days
            </p>
          </div>

          {/* Max episodes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Maximum episodes
            </label>
            <input
              type="number"
              min={1}
              max={100}
              value={maxEpisodes}
              onChange={(e) => setMaxEpisodes(parseInt(e.target.value) || 10)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              Limit briefing to {maxEpisodes} episodes
            </p>
          </div>

          {/* Ready only */}
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="readyOnly"
              checked={readyOnly}
              onChange={(e) => setReadyOnly(e.target.checked)}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="readyOnly" className="text-sm text-gray-700">
              Only include already-summarized episodes (recommended)
            </label>
          </div>

          {/* Exclude briefed */}
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="excludeBriefed"
              checked={excludeBriefed}
              onChange={(e) => setExcludeBriefed(e.target.checked)}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="excludeBriefed" className="text-sm text-gray-700">
              Exclude episodes already in a briefing
            </label>
          </div>

          {/* Preview button */}
          <button
            onClick={handlePreview}
            disabled={isLoadingPreview}
            className="w-full px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
          >
            {isLoadingPreview ? 'Loading preview...' : 'Preview selection'}
          </button>

          {/* Preview results */}
          {preview && (
            <div className="border border-gray-200 rounded-lg p-4 bg-gray-50">
              <h3 className="font-medium text-gray-900 mb-2">
                Preview: {preview.length} of {previewTotal} matching episodes
              </h3>
              {preview.length === 0 ? (
                <p className="text-sm text-gray-500">No episodes match the criteria</p>
              ) : (
                <ul className="space-y-1 max-h-48 overflow-y-auto">
                  {preview.map((ep) => (
                    <li key={ep.episode_id} className="text-sm">
                      <span className="font-medium text-gray-800">{ep.episode_title}</span>
                      <span className="text-gray-500"> - {ep.podcast_title}</span>
                      <span className={`ml-2 px-1.5 py-0.5 rounded text-xs ${
                        ep.state === 'summarized' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {ep.state}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <div className="p-6 border-t border-gray-200 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium rounded-lg text-gray-700 bg-gray-100 hover:bg-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isCreating || (preview !== null && preview.length === 0)}
            className={`
              px-4 py-2 text-sm font-medium rounded-lg transition-colors
              ${isCreating || (preview !== null && preview.length === 0)
                ? 'bg-primary-300 text-white cursor-not-allowed'
                : 'bg-primary-600 text-white hover:bg-primary-700'
              }
            `}
          >
            {isCreating ? 'Creating...' : 'Create Briefing'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Briefings() {
  const { data, isLoading, error } = useBriefings()
  const createMutation = useCreateBriefing()
  const deleteMutation = useDeleteBriefing()
  const previewMutation = usePreviewBriefing()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const handleDelete = async (briefingId: string) => {
    setDeletingId(briefingId)
    try {
      await deleteMutation.mutateAsync(briefingId)
    } finally {
      setDeletingId(null)
    }
  }

  const handleCreate = async (request: CreateBriefingRequest) => {
    await createMutation.mutateAsync(request)
    setShowCreateModal(false)
    previewMutation.reset()
  }

  const handlePreview = async (request: CreateBriefingRequest) => {
    await previewMutation.mutateAsync(request)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">Loading briefings...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-500">Error loading briefings: {error.message}</div>
      </div>
    )
  }

  const briefings = data?.briefings || []

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Briefings</h1>
          <p className="text-sm text-gray-500 mt-1">
            Daily readouts of new episodes from your inbox
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 transition-colors flex items-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New Briefing
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-gray-900">{briefings.length}</div>
          <div className="text-sm text-gray-500">Total Briefings</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-green-600">
            {briefings.filter(d => d.status === 'completed').length}
          </div>
          <div className="text-sm text-gray-500">Completed</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-orange-600">
            {briefings.filter(d => d.status === 'partial').length}
          </div>
          <div className="text-sm text-gray-500">Partial</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-red-600">
            {briefings.filter(d => d.status === 'failed').length}
          </div>
          <div className="text-sm text-gray-500">Failed</div>
        </div>
      </div>

      {/* Briefing list */}
      {briefings.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
          <div className="text-gray-400 mb-2">
            <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <p className="text-gray-500">No briefings yet</p>
          <p className="text-sm text-gray-400 mt-1">Create your first briefing to get started</p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="mt-4 px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 transition-colors"
          >
            Create Briefing
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {briefings.map((briefing) => (
            <BriefingCard
              key={briefing.id}
              briefing={briefing}
              onDelete={handleDelete}
              isDeleting={deletingId === briefing.id && deleteMutation.isPending}
            />
          ))}
        </div>
      )}

      {/* Info box */}
      <div className="mt-8 p-4 bg-gray-50 rounded-lg">
        <h3 className="font-medium text-gray-900 mb-2">About Briefings</h3>
        <div className="text-sm text-gray-600 space-y-2">
          <p>
            <span className="font-medium text-green-700">Completed</span> briefings have
            all episodes processed successfully.
          </p>
          <p>
            <span className="font-medium text-orange-700">Partial</span> briefings have
            some episodes that failed but still include available content.
          </p>
          <p>
            <span className="font-medium text-red-700">Failed</span> briefings could not
            be generated due to errors.
          </p>
        </div>
      </div>

      {/* Create modal */}
      <CreateBriefingModal
        isOpen={showCreateModal}
        onClose={() => {
          setShowCreateModal(false)
          previewMutation.reset()
        }}
        onSubmit={handleCreate}
        isCreating={createMutation.isPending}
        preview={previewMutation.data?.episodes || null}
        previewTotal={previewMutation.data?.total_matching || null}
        onPreview={handlePreview}
        isLoadingPreview={previewMutation.isPending}
      />
    </div>
  )
}
