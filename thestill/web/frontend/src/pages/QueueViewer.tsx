import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueueTasks, useBumpQueueTask, useCancelQueueTask } from '../hooks/useApi'
import type { QueuedTaskWithContext } from '../api/types'

// Stage colors for badges
const stageColors: Record<string, string> = {
  download: 'bg-blue-100 text-blue-700',
  downsample: 'bg-indigo-100 text-indigo-700',
  transcribe: 'bg-purple-100 text-purple-700',
  clean: 'bg-amber-100 text-amber-700',
  summarize: 'bg-green-100 text-green-700',
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const secs = seconds % 60
    return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`
  }
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`
}

function formatTimeAgo(dateStr: string | null): string {
  if (!dateStr) return 'Unknown'
  const date = new Date(dateStr)
  const now = new Date()
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000)

  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

interface TaskCardProps {
  task: QueuedTaskWithContext
  showBumpButton?: boolean
  showCancelButton?: boolean
  onBump?: (taskId: string) => void
  onCancel?: (taskId: string) => void
  isBumping?: boolean
  isCancelling?: boolean
}

function TaskCard({ task, showBumpButton = false, showCancelButton = false, onBump, onCancel, isBumping = false, isCancelling = false }: TaskCardProps) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
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
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${stageColors[task.stage] || 'bg-gray-100 text-gray-700'}`}
            >
              {task.stage}
            </span>
            {task.duration_formatted && (
              <span className="text-xs text-gray-500">{task.duration_formatted}</span>
            )}
            {task.retry_count > 0 && (
              <span className="text-xs text-yellow-600">Retry #{task.retry_count}</span>
            )}
          </div>

          {/* Time info */}
          <div className="mt-2 text-xs text-gray-400">
            {task.status === 'processing' && task.processing_time_seconds !== null && (
              <span>Processing for {formatDuration(task.processing_time_seconds)}</span>
            )}
            {task.status === 'pending' && task.time_in_queue_seconds !== null && (
              <span>In queue for {formatDuration(task.time_in_queue_seconds)}</span>
            )}
            {task.status === 'retry_scheduled' && task.next_retry_at && (
              <span>Retry scheduled for {formatTimeAgo(task.next_retry_at)}</span>
            )}
            {task.status === 'completed' && (
              <span className="flex flex-wrap gap-2">
                {task.wait_time_seconds !== null && (
                  <span>Waited {formatDuration(task.wait_time_seconds)}</span>
                )}
                {task.processing_time_seconds !== null && (
                  <span>Processed in {formatDuration(task.processing_time_seconds)}</span>
                )}
              </span>
            )}
          </div>
        </div>

        {/* Action buttons for pending tasks */}
        {(showBumpButton || showCancelButton) && (
          <div className="flex items-center gap-1">
            {showBumpButton && onBump && (
              <button
                onClick={() => onBump(task.task_id)}
                disabled={isBumping || isCancelling}
                title="Move to front of queue"
                className={`
                  p-2 rounded-lg transition-colors
                  ${isBumping || isCancelling
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'text-gray-500 hover:text-primary-600 hover:bg-primary-50'
                  }
                `}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 15l7-7 7 7"
                  />
                </svg>
              </button>
            )}
            {showCancelButton && onCancel && (
              <button
                onClick={() => onCancel(task.task_id)}
                disabled={isBumping || isCancelling}
                title="Cancel task"
                className={`
                  p-2 rounded-lg transition-colors
                  ${isBumping || isCancelling
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'text-gray-500 hover:text-red-600 hover:bg-red-50'
                  }
                `}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

interface StatCardProps {
  label: string
  value: number
  color?: 'default' | 'blue' | 'green' | 'yellow'
}

function StatCard({ label, value, color = 'default' }: StatCardProps) {
  const colorClasses = {
    default: 'text-gray-900',
    blue: 'text-blue-600',
    green: 'text-green-600',
    yellow: 'text-yellow-600',
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className={`text-2xl font-bold ${colorClasses[color]}`}>{value}</div>
      <div className="text-sm text-gray-500">{label}</div>
    </div>
  )
}

export default function QueueViewer() {
  const { data, isLoading, error } = useQueueTasks(10)
  const bumpMutation = useBumpQueueTask()
  const cancelMutation = useCancelQueueTask()
  const [bumpingTaskId, setBumpingTaskId] = useState<string | null>(null)
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null)

  const handleBump = async (taskId: string) => {
    setBumpingTaskId(taskId)
    try {
      await bumpMutation.mutateAsync(taskId)
    } finally {
      setBumpingTaskId(null)
    }
  }

  const handleCancel = async (taskId: string) => {
    setCancellingTaskId(taskId)
    try {
      await cancelMutation.mutateAsync(taskId)
    } finally {
      setCancellingTaskId(null)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">Loading queue...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-500">Error loading queue: {error.message}</div>
      </div>
    )
  }

  const {
    worker_running,
    processing_task,
    pending_tasks,
    retry_scheduled_tasks,
    completed_tasks,
    pending_count,
    processing_count,
    retry_scheduled_count,
    completed_shown,
  } = data || {
    worker_running: false,
    processing_task: null,
    pending_tasks: [],
    retry_scheduled_tasks: [],
    completed_tasks: [],
    pending_count: 0,
    processing_count: 0,
    retry_scheduled_count: 0,
    completed_shown: 0,
  }

  const isQueueIdle =
    !processing_task && pending_count === 0 && retry_scheduled_count === 0

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Task Queue</h1>
          <p className="text-sm text-gray-500 mt-1">
            Monitor background processing tasks
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`px-3 py-1 rounded-full text-sm font-medium ${
              worker_running
                ? 'bg-green-100 text-green-700'
                : 'bg-red-100 text-red-700'
            }`}
          >
            Worker: {worker_running ? 'Running' : 'Stopped'}
          </span>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Processing" value={processing_count} color="blue" />
        <StatCard label="Pending" value={pending_count} />
        <StatCard label="Retry Scheduled" value={retry_scheduled_count} color="yellow" />
        <StatCard label="Completed (recent)" value={completed_shown} color="green" />
      </div>

      {/* Idle state */}
      {isQueueIdle ? (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center mb-6">
          <div className="text-gray-400 mb-2">
            <svg
              className="w-12 h-12 mx-auto"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <p className="text-gray-500">Queue is idle</p>
          <p className="text-sm text-gray-400 mt-1">No tasks are currently processing</p>
        </div>
      ) : (
        <>
          {/* Currently Processing */}
          {processing_task && (
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3 flex items-center gap-2">
                <span className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                Currently Processing
              </h2>
              <TaskCard task={processing_task} />
            </div>
          )}

          {/* Pending Tasks */}
          {pending_tasks.length > 0 && (
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">
                Pending ({pending_count})
              </h2>
              <div className="space-y-3">
                {pending_tasks.map((task) => (
                  <TaskCard
                    key={task.task_id}
                    task={task}
                    showBumpButton
                    showCancelButton
                    onBump={handleBump}
                    onCancel={handleCancel}
                    isBumping={bumpingTaskId === task.task_id}
                    isCancelling={cancellingTaskId === task.task_id}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Retry Scheduled Tasks */}
          {retry_scheduled_tasks.length > 0 && (
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">
                Retry Scheduled ({retry_scheduled_count})
              </h2>
              <div className="space-y-3">
                {retry_scheduled_tasks.map((task) => (
                  <TaskCard key={task.task_id} task={task} />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Recently Completed */}
      {completed_tasks.length > 0 && (
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-3">
            Recently Completed ({completed_shown})
          </h2>
          <div className="space-y-3">
            {completed_tasks.map((task) => (
              <TaskCard key={task.task_id} task={task} />
            ))}
          </div>
        </div>
      )}

      {/* Help text */}
      <div className="mt-8 p-4 bg-gray-50 rounded-lg">
        <h3 className="font-medium text-gray-900 mb-2">About the Task Queue</h3>
        <div className="text-sm text-gray-600 space-y-2">
          <p>
            Tasks are processed in order of priority. Use the{' '}
            <span className="inline-flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 15l7-7 7 7"
                />
              </svg>
              bump
            </span>{' '}
            button to move a task to the front of the queue, or the{' '}
            <span className="inline-flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
              cancel
            </span>{' '}
            button to remove a pending task from the queue.
          </p>
          <p>
            <span className="font-medium text-yellow-700">Retry scheduled</span> tasks
            will automatically retry after a backoff period.
          </p>
        </div>
      </div>
    </div>
  )
}
