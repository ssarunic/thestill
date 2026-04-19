import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueueTasks, useBumpQueueTask, useCancelQueueTask } from '../hooks/useApi'
import type { PipelineStage, QueuedTaskWithContext, StageWorkerStatus } from '../api/types'

// Pipeline order — lanes are always displayed in this order
const STAGE_ORDER: PipelineStage[] = ['download', 'downsample', 'transcribe', 'clean', 'summarize']

// Stage colors for badges and lane accents
const stageColors: Record<PipelineStage, string> = {
  download: 'bg-blue-100 text-blue-700',
  downsample: 'bg-indigo-100 text-indigo-700',
  transcribe: 'bg-purple-100 text-purple-700',
  clean: 'bg-amber-100 text-amber-700',
  summarize: 'bg-green-100 text-green-700',
}

const stageAccent: Record<PipelineStage, string> = {
  download: 'border-blue-300',
  downsample: 'border-indigo-300',
  transcribe: 'border-purple-300',
  clean: 'border-amber-300',
  summarize: 'border-green-300',
}

const stageLabel: Record<PipelineStage, string> = {
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcribe',
  clean: 'Clean',
  summarize: 'Summarize',
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
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
  compact?: boolean
}

function TaskCard({
  task,
  showBumpButton = false,
  showCancelButton = false,
  onBump,
  onCancel,
  isBumping = false,
  isCancelling = false,
  compact = false,
}: TaskCardProps) {
  return (
    <div
      className={`bg-white rounded-md border border-gray-200 shadow-sm ${compact ? 'p-2.5' : 'p-4'}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <Link
            to={`/podcasts/${task.podcast_slug}/episodes/${task.episode_slug}`}
            className={`font-medium text-gray-900 hover:text-primary-600 line-clamp-1 ${compact ? 'text-sm' : ''}`}
          >
            {task.episode_title}
          </Link>
          <p className={`text-gray-500 mt-0.5 line-clamp-1 ${compact ? 'text-xs' : 'text-sm'}`}>
            {task.podcast_title}
          </p>

          {/* Per-lane cards omit the stage badge (stage is implicit in the lane) */}
          {!compact && (
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
          )}
          {compact && task.retry_count > 0 && (
            <span className="text-xs text-yellow-600 mt-1 inline-block">
              Retry #{task.retry_count}
            </span>
          )}

          <div className={`text-gray-400 ${compact ? 'mt-1 text-xs' : 'mt-2 text-xs'}`}>
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

        {(showBumpButton || showCancelButton) && (
          <div className="flex items-center gap-1">
            {showBumpButton && onBump && (
              <button
                onClick={() => onBump(task.task_id)}
                disabled={isBumping || isCancelling}
                title="Move to front of queue"
                className={`
                  p-1.5 rounded-md transition-colors
                  ${isBumping || isCancelling
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'text-gray-500 hover:text-primary-600 hover:bg-primary-50'
                  }
                `}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                </svg>
              </button>
            )}
            {showCancelButton && onCancel && (
              <button
                onClick={() => onCancel(task.task_id)}
                disabled={isBumping || isCancelling}
                title="Cancel task"
                className={`
                  p-1.5 rounded-md transition-colors
                  ${isBumping || isCancelling
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'text-gray-500 hover:text-red-600 hover:bg-red-50'
                  }
                `}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

interface StageLaneProps {
  stage: StageWorkerStatus
  processing: QueuedTaskWithContext[]
  pending: QueuedTaskWithContext[]
  onBump: (taskId: string) => void
  onCancel: (taskId: string) => void
  bumpingTaskId: string | null
  cancellingTaskId: string | null
}

function StageLane({
  stage,
  processing,
  pending,
  onBump,
  onCancel,
  bumpingTaskId,
  cancellingTaskId,
}: StageLaneProps) {
  const hasCapacity = stage.capacity > 0
  const utilization = hasCapacity ? stage.active / stage.capacity : 0
  const isBusy = stage.active > 0
  // Flag the lane as backpressured when pending depth is >=3x the pool size.
  // capacity <= 0 means the backend didn't report this stage, so we can't judge backpressure.
  const isBackpressured = hasCapacity && stage.pending > 0 && stage.pending >= stage.capacity * 3
  const isIdle = !isBusy && stage.pending === 0 && stage.retry_scheduled === 0

  return (
    <div className={`bg-white rounded-lg border-l-4 ${stageAccent[stage.stage]} border-t border-r border-b border-gray-200 overflow-hidden`}>
      {/* Lane header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-50 border-b border-gray-200">
        <div className="flex items-center gap-2">
          {isBusy && (
            <span className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" aria-hidden />
          )}
          <h3 className="font-semibold text-gray-900">{stageLabel[stage.stage]}</h3>
          <span
            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              isIdle
                ? 'bg-gray-100 text-gray-500'
                : isBackpressured
                  ? 'bg-red-100 text-red-700'
                  : 'bg-blue-50 text-blue-700'
            }`}
          >
            {hasCapacity ? `${stage.active}/${stage.capacity} busy` : `${stage.active} busy`}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-600">
          <span className={isBackpressured ? 'text-red-700 font-medium' : ''}>
            {stage.pending} pending
          </span>
          {stage.retry_scheduled > 0 && (
            <span className="text-yellow-600">{stage.retry_scheduled} retry</span>
          )}
          {stage.capacity > 0 && (
            <div className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden" title={`${Math.round(utilization * 100)}% utilization`}>
              <div
                className={`h-full ${isBackpressured ? 'bg-red-500' : 'bg-blue-500'}`}
                style={{ width: `${Math.min(100, utilization * 100)}%` }}
              />
            </div>
          )}
        </div>
      </div>

      {/* Lane body */}
      {isIdle ? (
        <div className="px-4 py-3 text-xs text-gray-400">Idle</div>
      ) : (
        <div className="p-3 space-y-2">
          {processing.map((task) => (
            <TaskCard key={task.task_id} task={task} compact />
          ))}
          {pending.map((task) => (
            <TaskCard
              key={task.task_id}
              task={task}
              compact
              showBumpButton
              showCancelButton
              onBump={onBump}
              onCancel={onCancel}
              isBumping={bumpingTaskId === task.task_id}
              isCancelling={cancellingTaskId === task.task_id}
            />
          ))}
        </div>
      )}
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
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className={`text-xl font-bold ${colorClasses[color]}`}>{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
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
    stages = [],
    processing_tasks,
    pending_tasks,
    retry_scheduled_tasks,
    completed_tasks,
    pending_count,
    processing_count,
    retry_scheduled_count,
    completed_shown,
  } = data || {
    worker_running: false,
    stages: [],
    processing_tasks: [],
    pending_tasks: [],
    retry_scheduled_tasks: [],
    completed_tasks: [],
    pending_count: 0,
    processing_count: 0,
    retry_scheduled_count: 0,
    completed_shown: 0,
  }

  // Group tasks by stage for the swimlanes
  const processingByStage = new Map<PipelineStage, QueuedTaskWithContext[]>()
  for (const task of processing_tasks) {
    const list = processingByStage.get(task.stage) ?? []
    list.push(task)
    processingByStage.set(task.stage, list)
  }
  const pendingByStage = new Map<PipelineStage, QueuedTaskWithContext[]>()
  for (const task of pending_tasks) {
    const list = pendingByStage.get(task.stage) ?? []
    list.push(task)
    pendingByStage.set(task.stage, list)
  }

  // Ensure we always render a lane per pipeline stage, even if the backend
  // omits one (e.g. during a fresh install). Fall back to a synthetic status.
  const stagesByName = new Map(stages.map((s) => [s.stage, s]))
  const orderedStages: StageWorkerStatus[] = STAGE_ORDER.map(
    (name) =>
      stagesByName.get(name) ?? {
        stage: name,
        active: 0,
        capacity: 0,
        pending: pendingByStage.get(name)?.length ?? 0,
        retry_scheduled: 0,
      },
  )

  // Collapse idle lanes into a single line to reduce vertical space.
  const activeLanes = orderedStages.filter(
    (s) => s.active > 0 || s.pending > 0 || s.retry_scheduled > 0,
  )
  const idleLanes = orderedStages.filter(
    (s) => s.active === 0 && s.pending === 0 && s.retry_scheduled === 0,
  )

  const totalCapacity = orderedStages.reduce((sum, s) => sum + s.capacity, 0)
  const totalActive = orderedStages.reduce((sum, s) => sum + s.active, 0)

  const isQueueIdle = activeLanes.length === 0 && retry_scheduled_count === 0

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Task Queue</h1>
          <p className="text-sm text-gray-500 mt-1">
            Each pipeline stage runs on its own worker pool — slow stages no longer block fast ones.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`px-3 py-1 rounded-full text-sm font-medium ${
              worker_running ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
            }`}
          >
            Worker: {worker_running ? 'Running' : 'Stopped'}
          </span>
        </div>
      </div>

      {/* Aggregate stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <StatCard label={`Processing (${totalActive}/${totalCapacity})`} value={processing_count} color="blue" />
        <StatCard label="Pending" value={pending_count} />
        <StatCard label="Retry Scheduled" value={retry_scheduled_count} color="yellow" />
        <StatCard label="Completed (recent)" value={completed_shown} color="green" />
        <StatCard label="Active Stages" value={activeLanes.length} />
      </div>

      {/* Stage swimlanes */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-3">Pipeline Stages</h2>
        {isQueueIdle ? (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
            <div className="text-gray-400 mb-2">
              <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
            </div>
            <p className="text-gray-500">All stages idle</p>
            <p className="text-sm text-gray-400 mt-1">No tasks are currently processing</p>
          </div>
        ) : (
          <div className="space-y-3">
            {activeLanes.map((stage) => (
              <StageLane
                key={stage.stage}
                stage={stage}
                processing={processingByStage.get(stage.stage) ?? []}
                pending={pendingByStage.get(stage.stage) ?? []}
                onBump={handleBump}
                onCancel={handleCancel}
                bumpingTaskId={bumpingTaskId}
                cancellingTaskId={cancellingTaskId}
              />
            ))}
            {idleLanes.length > 0 && (
              <div className="bg-gray-50 rounded-lg border border-gray-200 px-4 py-2 text-xs text-gray-500">
                Idle:{' '}
                {idleLanes.map((s, idx) => (
                  <span key={s.stage}>
                    {idx > 0 && ', '}
                    <span className="font-medium text-gray-600">{stageLabel[s.stage]}</span>{' '}
                    <span className="text-gray-400">({s.capacity})</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Retry Scheduled (status-grouped, stage-agnostic) */}
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
            Each pipeline stage — download, downsample, transcribe, clean, summarize — has its own
            worker pool. A slow <span className="font-medium">transcribe</span> job no longer holds
            up a fast <span className="font-medium">clean</span> job on another episode.
          </p>
          <p>
            A lane turns <span className="text-red-700 font-medium">red</span> when pending depth
            exceeds 3× its pool size — a hint that stage is a bottleneck and could use more
            workers. Tune capacity per stage via{' '}
            <code className="px-1 py-0.5 bg-gray-200 rounded text-xs">DOWNLOAD_PARALLEL_JOBS</code>,{' '}
            <code className="px-1 py-0.5 bg-gray-200 rounded text-xs">TRANSCRIBE_PARALLEL_JOBS</code>, etc.
          </p>
          <p>
            Use the{' '}
            <span className="inline-flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
              bump
            </span>{' '}
            button to move a task to the front of its stage's queue, or the{' '}
            <span className="inline-flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
              cancel
            </span>{' '}
            button to remove a pending task.
          </p>
        </div>
      </div>
    </div>
  )
}
