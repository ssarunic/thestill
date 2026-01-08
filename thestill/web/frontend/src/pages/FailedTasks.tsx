import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDLQTasks, useRetryDLQTask, useSkipDLQTask, useRetryAllDLQTasks } from '../hooks/useApi'
import type { DLQTask, FailureType } from '../api/types'
import FailureDetailsModal from '../components/FailureDetailsModal'

// Stage colors for badges
const stageColors: Record<string, string> = {
  download: 'bg-blue-100 text-blue-700',
  downsample: 'bg-indigo-100 text-indigo-700',
  transcribe: 'bg-purple-100 text-purple-700',
  clean: 'bg-amber-100 text-amber-700',
  summarize: 'bg-green-100 text-green-700',
}

// Error type colors
const errorTypeColors: Record<string, string> = {
  transient: 'bg-yellow-100 text-yellow-700',
  fatal: 'bg-red-100 text-red-700',
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface DLQTaskCardProps {
  task: DLQTask
  onRetry: (taskId: string) => void
  onSkip: (taskId: string) => void
  isRetrying: boolean
  isSkipping: boolean
}

function DLQTaskCard({ task, onRetry, onSkip, isRetrying, isSkipping }: DLQTaskCardProps) {
  const [showDetails, setShowDetails] = useState(false)
  const [showModal, setShowModal] = useState(false)

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
      <div className="p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            {/* Episode title and link */}
            <Link
              to={`/podcasts/${task.podcast_slug}/episodes/${task.episode_slug}`}
              className="font-medium text-gray-900 hover:text-primary-600 line-clamp-1"
            >
              {task.episode_title}
            </Link>
            <p className="text-sm text-gray-500 mt-1">{task.podcast_title}</p>

            {/* Badges */}
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${stageColors[task.stage] || 'bg-gray-100 text-gray-700'}`}>
                {task.stage}
              </span>
              {task.error_type && (
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${errorTypeColors[task.error_type]}`}>
                  {task.error_type}
                </span>
              )}
              <span className="text-xs text-gray-500">
                Retries: {task.retry_count}/{task.max_retries}
              </span>
            </div>

            {/* Error preview */}
            {task.error_message && (
              <div className="mt-2">
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setShowDetails(!showDetails)}
                    className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
                  >
                    <svg
                      className={`w-3 h-3 transition-transform ${showDetails ? 'rotate-90' : ''}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                    {showDetails ? 'Hide error' : 'Show error'}
                  </button>
                  <button
                    onClick={() => setShowModal(true)}
                    className="text-xs text-primary-600 hover:text-primary-700 flex items-center gap-1"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                    </svg>
                    Expand
                  </button>
                </div>
                {showDetails && (
                  <pre className="mt-2 p-2 bg-gray-50 rounded text-xs text-gray-700 overflow-x-auto whitespace-pre-wrap break-words">
                    {task.error_message}
                  </pre>
                )}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-2">
            <button
              onClick={() => onRetry(task.task_id)}
              disabled={isRetrying || isSkipping}
              className={`
                px-3 py-1.5 text-sm font-medium rounded-lg transition-colors
                ${isRetrying
                  ? 'bg-blue-100 text-blue-600 cursor-not-allowed'
                  : 'bg-blue-600 text-white hover:bg-blue-700'
                }
              `}
            >
              {isRetrying ? 'Retrying...' : 'Retry'}
            </button>
            <button
              onClick={() => onSkip(task.task_id)}
              disabled={isRetrying || isSkipping}
              className={`
                px-3 py-1.5 text-sm font-medium rounded-lg transition-colors
                ${isSkipping
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'text-gray-600 bg-gray-100 hover:bg-gray-200'
                }
              `}
            >
              {isSkipping ? 'Skipping...' : 'Skip'}
            </button>
          </div>
        </div>

        {/* Timestamp */}
        <div className="mt-3 text-xs text-gray-400">
          Failed at {formatDate(task.completed_at)}
        </div>
      </div>

      {/* Failure details modal */}
      <FailureDetailsModal
        isOpen={showModal}
        onClose={() => setShowModal(false)}
        episodeTitle={task.episode_title}
        episodeSlug={task.episode_slug}
        podcastSlug={task.podcast_slug}
        podcastTitle={task.podcast_title}
        failedAtStage={task.stage}
        failureReason={task.error_message}
        failureType={task.error_type as FailureType}
        failedAt={task.completed_at}
        retryCount={task.retry_count}
        maxRetries={task.max_retries}
        onRetry={() => {
          onRetry(task.task_id)
          setShowModal(false)
        }}
        isRetrying={isRetrying}
      />
    </div>
  )
}

export default function FailedTasks() {
  const { data, isLoading, error } = useDLQTasks()
  const retryMutation = useRetryDLQTask()
  const skipMutation = useSkipDLQTask()
  const retryAllMutation = useRetryAllDLQTasks()
  const [selectedTasks, setSelectedTasks] = useState<Set<string>>(new Set())
  const [processingTasks, setProcessingTasks] = useState<Set<string>>(new Set())

  const handleRetry = async (taskId: string) => {
    setProcessingTasks(prev => new Set(prev).add(taskId))
    try {
      await retryMutation.mutateAsync(taskId)
    } finally {
      setProcessingTasks(prev => {
        const next = new Set(prev)
        next.delete(taskId)
        return next
      })
    }
  }

  const handleSkip = async (taskId: string) => {
    setProcessingTasks(prev => new Set(prev).add(taskId))
    try {
      await skipMutation.mutateAsync(taskId)
    } finally {
      setProcessingTasks(prev => {
        const next = new Set(prev)
        next.delete(taskId)
        return next
      })
    }
  }

  const handleRetryAll = async () => {
    const taskIds = selectedTasks.size > 0 ? Array.from(selectedTasks) : undefined
    await retryAllMutation.mutateAsync(taskIds)
    setSelectedTasks(new Set())
  }

  const toggleTaskSelection = (taskId: string) => {
    setSelectedTasks(prev => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }

  const selectAllTasks = () => {
    if (data?.tasks) {
      setSelectedTasks(new Set(data.tasks.map(t => t.task_id)))
    }
  }

  const deselectAllTasks = () => {
    setSelectedTasks(new Set())
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">Loading failed tasks...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-500">Error loading failed tasks: {error.message}</div>
      </div>
    )
  }

  const tasks = data?.tasks || []

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Failed Tasks</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tasks that failed and need manual intervention
          </p>
        </div>
        {tasks.length > 0 && (
          <div className="flex items-center gap-2">
            {selectedTasks.size > 0 ? (
              <button
                onClick={deselectAllTasks}
                className="text-sm text-gray-600 hover:text-gray-800"
              >
                Deselect all
              </button>
            ) : (
              <button
                onClick={selectAllTasks}
                className="text-sm text-gray-600 hover:text-gray-800"
              >
                Select all
              </button>
            )}
            <button
              onClick={handleRetryAll}
              disabled={retryAllMutation.isPending}
              className={`
                px-4 py-2 text-sm font-medium rounded-lg transition-colors
                ${retryAllMutation.isPending
                  ? 'bg-blue-100 text-blue-600 cursor-not-allowed'
                  : 'bg-blue-600 text-white hover:bg-blue-700'
                }
              `}
            >
              {retryAllMutation.isPending
                ? 'Retrying...'
                : selectedTasks.size > 0
                  ? `Retry ${selectedTasks.size} selected`
                  : 'Retry all'
              }
            </button>
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-gray-900">{tasks.length}</div>
          <div className="text-sm text-gray-500">Total Failed</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-yellow-600">
            {tasks.filter(t => t.error_type === 'transient').length}
          </div>
          <div className="text-sm text-gray-500">Transient Errors</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-red-600">
            {tasks.filter(t => t.error_type === 'fatal').length}
          </div>
          <div className="text-sm text-gray-500">Fatal Errors</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-2xl font-bold text-gray-600">
            {selectedTasks.size}
          </div>
          <div className="text-sm text-gray-500">Selected</div>
        </div>
      </div>

      {/* Task list */}
      {tasks.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
          <div className="text-gray-400 mb-2">
            <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-gray-500">No failed tasks</p>
          <p className="text-sm text-gray-400 mt-1">All tasks are running smoothly</p>
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map((task) => (
            <div key={task.task_id} className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={selectedTasks.has(task.task_id)}
                onChange={() => toggleTaskSelection(task.task_id)}
                className="mt-4 w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
              />
              <div className="flex-1">
                <DLQTaskCard
                  task={task}
                  onRetry={handleRetry}
                  onSkip={handleSkip}
                  isRetrying={processingTasks.has(task.task_id) && retryMutation.isPending}
                  isSkipping={processingTasks.has(task.task_id) && skipMutation.isPending}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Help text */}
      <div className="mt-8 p-4 bg-gray-50 rounded-lg">
        <h3 className="font-medium text-gray-900 mb-2">About Failed Tasks</h3>
        <div className="text-sm text-gray-600 space-y-2">
          <p>
            <span className="font-medium text-yellow-700">Transient errors</span> are temporary failures
            (network issues, rate limits, etc.) that may succeed on retry.
          </p>
          <p>
            <span className="font-medium text-red-700">Fatal errors</span> indicate permanent problems
            (invalid files, missing content, etc.) that likely need investigation.
          </p>
          <p>
            <span className="font-medium">Skip</span> marks a task as resolved without processing.
            Use this if you've manually fixed the issue or decided to skip the episode.
          </p>
        </div>
      </div>
    </div>
  )
}
