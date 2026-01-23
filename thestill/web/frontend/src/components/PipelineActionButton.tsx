import { useState, useEffect, useRef, useCallback } from 'react'
import { useQueuePipelineTask, useEpisodeTasks, useRunPipeline, useCancelPipeline } from '../hooks/useApi'
import type { PipelineStage, ExtendedPipelineTaskStatus } from '../api/types'
import PipelineStepper from './PipelineStepper'

interface PipelineActionButtonProps {
  podcastSlug: string
  episodeSlug: string
  episodeId: string
  episodeState: string
  onTaskComplete?: (stage: PipelineStage) => void
}

// Progress update from SSE
interface ProgressUpdate {
  stage: string
  progress_pct: number
  message: string
  estimated_remaining_seconds: number | null
}

// Map episode state to the next action
const stateToAction: Record<string, { stage: PipelineStage; label: string; icon: string }> = {
  discovered: { stage: 'download', label: 'Download', icon: 'download' },
  downloaded: { stage: 'downsample', label: 'Downsample', icon: 'waveform' },
  downsampled: { stage: 'transcribe', label: 'Transcribe', icon: 'mic' },
  transcribed: { stage: 'clean', label: 'Clean', icon: 'sparkles' },
  cleaned: { stage: 'summarize', label: 'Summarize', icon: 'document' },
}


// Human-readable labels for transcription stages
const STAGE_LABELS: Record<string, string> = {
  pending: 'Starting...',
  loading_model: 'Loading model...',
  transcribing: 'Transcribing...',
  aligning: 'Aligning timestamps...',
  diarizing: 'Identifying speakers...',
  formatting: 'Formatting...',
  completed: 'Complete!',
  failed: 'Failed',
  processing: 'Processing...',
}

// Color scheme for different states
const stageColors: Record<PipelineStage, string> = {
  download: 'bg-blue-600 hover:bg-blue-700',
  downsample: 'bg-indigo-600 hover:bg-indigo-700',
  transcribe: 'bg-purple-600 hover:bg-purple-700',
  clean: 'bg-amber-600 hover:bg-amber-700',
  summarize: 'bg-green-600 hover:bg-green-700',
}

// Format seconds into human-readable time string
function formatTimeRemaining(seconds: number): string {
  if (seconds < 60) {
    return `~${Math.ceil(seconds)}s remaining`
  }
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60)
    const secs = Math.ceil(seconds % 60)
    return secs > 0 ? `~${mins}m ${secs}s remaining` : `~${mins}m remaining`
  }
  const hours = Math.floor(seconds / 3600)
  const mins = Math.ceil((seconds % 3600) / 60)
  return mins > 0 ? `~${hours}h ${mins}m remaining` : `~${hours}h remaining`
}

// Format countdown time (e.g., "4m 30s")
function formatCountdown(seconds: number): string {
  if (seconds <= 0) return 'now'
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.ceil(seconds % 60)
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

function getIcon(iconType: string) {
  switch (iconType) {
    case 'download':
      return (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      )
    case 'waveform':
      return (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
        </svg>
      )
    case 'mic':
      return (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
        </svg>
      )
    case 'sparkles':
      return (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
        </svg>
      )
    case 'document':
      return (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      )
    default:
      return null
  }
}

function SpinnerIcon() {
  return (
    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  )
}

function QueueIcon() {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function ChevronDownIcon() {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  )
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="w-32 h-2 bg-gray-200 rounded-full overflow-hidden">
      <div
        className="h-full bg-purple-600 transition-all duration-300 ease-out"
        style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
      />
    </div>
  )
}


// Retry countdown component
function RetryCountdown({
  nextRetryAt,
  retryCount,
  maxRetries,
  lastError,
  onCancel,
}: {
  nextRetryAt: string
  retryCount: number
  maxRetries: number
  lastError: string | null
  onCancel: () => void
}) {
  const [secondsRemaining, setSecondsRemaining] = useState(0)

  useEffect(() => {
    const calculateRemaining = () => {
      const retryTime = new Date(nextRetryAt).getTime()
      const now = Date.now()
      return Math.max(0, Math.floor((retryTime - now) / 1000))
    }

    setSecondsRemaining(calculateRemaining())

    const interval = setInterval(() => {
      const remaining = calculateRemaining()
      setSecondsRemaining(remaining)
      if (remaining <= 0) {
        clearInterval(interval)
      }
    }, 1000)

    return () => clearInterval(interval)
  }, [nextRetryAt])

  return (
    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <div className="text-yellow-500 mt-0.5">
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clipRule="evenodd" />
            </svg>
          </div>
          <div>
            <div className="text-sm font-medium text-yellow-800">
              Retry scheduled
            </div>
            <div className="text-sm text-yellow-700">
              Attempt {retryCount + 1}/{maxRetries} in{' '}
              <span className="font-mono font-medium">{formatCountdown(secondsRemaining)}</span>
            </div>
            {lastError && (
              <div className="text-xs text-yellow-600 mt-1" title={lastError}>
                Last error: {lastError.length > 50 ? lastError.slice(0, 50) + '...' : lastError}
              </div>
            )}
          </div>
        </div>
        <button
          onClick={onCancel}
          className="text-xs px-2 py-1 text-yellow-700 hover:text-yellow-900 hover:bg-yellow-100 rounded transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function PipelineActionButton({
  podcastSlug,
  episodeSlug,
  episodeId,
  episodeState,
  onTaskComplete,
}: PipelineActionButtonProps) {
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressUpdate | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const { mutate: queueTask, isPending } = useQueuePipelineTask(podcastSlug, episodeSlug)
  const { mutate: runFullPipeline, isPending: isPipelinePending } = useRunPipeline(podcastSlug, episodeSlug)
  const { mutate: cancelPipelineMutation } = useCancelPipeline()
  const { data: tasksData } = useEpisodeTasks(episodeId)
  const eventSourceRef = useRef<EventSource | null>(null)

  // Track previous active task to detect completion
  const prevActiveTaskRef = useRef<{ id: string; stage: PipelineStage } | null>(null)

  // Get action for current state
  const action = stateToAction[episodeState]

  // Check if there's already an active task
  const activeTask = tasksData?.tasks?.find(
    (t) => t.status === 'pending' || t.status === 'processing'
  )

  // Check for retry_scheduled task
  const retryScheduledTask = tasksData?.tasks?.find(
    (t) => (t.status as ExtendedPipelineTaskStatus) === 'retry_scheduled'
  )

  // Check if running a full pipeline (has metadata.run_full_pipeline)
  const isPipelineRunning = tasksData?.tasks?.some(
    (t) =>
      (t.status === 'pending' || t.status === 'processing') &&
      (t as any).metadata?.run_full_pipeline
  )

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Connect to SSE when there's an active transcribe task
  useEffect(() => {
    if (activeTask && activeTask.stage === 'transcribe' && activeTask.status === 'processing') {
      // Connect to SSE for progress updates
      const taskId = activeTask.id
      let reconnectAttempts = 0
      const maxReconnectAttempts = 5
      let eventSource: EventSource | null = null

      const connect = () => {
        eventSource = new EventSource(`/api/commands/task/${taskId}/progress`)
        eventSourceRef.current = eventSource

        eventSource.onmessage = (event) => {
          try {
            const data: ProgressUpdate = JSON.parse(event.data)
            setProgress(data)
            // Reset reconnect attempts on successful message
            reconnectAttempts = 0

            // Check if task completed or failed
            if (data.stage === 'completed' || data.stage === 'failed') {
              eventSource?.close()
              eventSourceRef.current = null
              // Clear progress after a short delay
              setTimeout(() => setProgress(null), 2000)
            }
          } catch (e) {
            console.error('Failed to parse SSE data:', e)
          }
        }

        eventSource.onerror = () => {
          // Close current connection
          eventSource?.close()
          eventSourceRef.current = null

          // Retry connection if not max attempts
          if (reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts++
            // Exponential backoff: 1s, 2s, 4s, 8s, 16s
            const delay = Math.pow(2, reconnectAttempts - 1) * 1000
            setTimeout(connect, delay)
          }
        }
      }

      connect()

      return () => {
        eventSource?.close()
        eventSourceRef.current = null
      }
    } else {
      // Clear progress when no active transcribe task
      setProgress(null)
    }
  }, [activeTask?.id, activeTask?.stage, activeTask?.status])

  // Detect when a task completes and notify parent
  useEffect(() => {
    const prevTask = prevActiveTaskRef.current

    // If we had an active task before but not now, check if it completed
    if (prevTask && !activeTask) {
      const completedTask = tasksData?.tasks?.find(
        (t) => t.id === prevTask.id && t.status === 'completed'
      )
      if (completedTask && onTaskComplete) {
        onTaskComplete(completedTask.stage)
      }
    }

    // Update ref with current active task
    prevActiveTaskRef.current = activeTask
      ? { id: activeTask.id, stage: activeTask.stage }
      : null
  }, [activeTask, tasksData?.tasks, onTaskComplete])

  // Check for failed task for the NEXT stage (the action we're about to take)
  // Only show if there's no completed task for that stage that supersedes it
  const recentFailedTask = action
    ? tasksData?.tasks?.find((t) => {
        if (t.stage !== action.stage || t.status !== 'failed') return false
        // Check if there's a completed task for the same stage that's newer
        const hasNewerSuccess = tasksData?.tasks?.some(
          (other) =>
            other.stage === t.stage &&
            other.status === 'completed' &&
            other.created_at &&
            t.created_at &&
            other.created_at > t.created_at
        )
        return !hasNewerSuccess
      })
    : null

  // Clear error after 5 seconds
  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 5000)
      return () => clearTimeout(timer)
    }
  }, [error])

  const handleSingleStep = useCallback(() => {
    if (!action) return
    setError(null)
    setDropdownOpen(false)
    queueTask(action.stage, {
      onError: (err: Error) => {
        setError(err.message)
      },
    })
  }, [action, queueTask])

  const handleFullPipeline = useCallback(() => {
    setError(null)
    setDropdownOpen(false)
    runFullPipeline('summarized', {
      onError: (err: Error) => {
        setError(err.message)
      },
    })
  }, [runFullPipeline])

  const handleCancelPipeline = useCallback(() => {
    cancelPipelineMutation(episodeId, {
      onError: (err: Error) => {
        setError(err.message)
      },
    })
  }, [cancelPipelineMutation, episodeId])

  // If already summarized, don't show any action (Ready badge shown elsewhere)
  if (episodeState === 'summarized') {
    return null
  }

  // If no action available, return null
  if (!action) {
    return null
  }

  const isDisabled = isPending || isPipelinePending || !!activeTask || !!retryScheduledTask
  const isProcessing = activeTask?.status === 'processing'

  // Show retry countdown if task is scheduled for retry
  if (retryScheduledTask) {
    const task = retryScheduledTask as any
    return (
      <div className="flex flex-col gap-2">
        <RetryCountdown
          nextRetryAt={task.next_retry_at}
          retryCount={task.retry_count}
          maxRetries={task.max_retries}
          lastError={task.last_error || task.error_message}
          onCancel={handleCancelPipeline}
        />
        {error && (
          <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
            {error}
          </div>
        )}
      </div>
    )
  }

  // Show pipeline progress visualization during full pipeline run
  if (isPipelineRunning && activeTask) {
    // Determine starting stage from episode state
    const startingStage = stateToAction[episodeState]?.stage || 'download'

    return (
      <div className="flex flex-col gap-3">
        <PipelineStepper
          currentStage={activeTask.stage}
          startingStage={startingStage}
          progress={progress}
          isProcessing={activeTask.status === 'processing'}
        />
        <button
          onClick={handleCancelPipeline}
          className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1 self-start"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
          Cancel Pipeline
        </button>
        {error && (
          <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
            {error}
          </div>
        )}
      </div>
    )
  }

  // Show processing/pending status with progress for transcribe (single step mode)
  if (activeTask) {
    const stageLabel = activeTask.stage.charAt(0).toUpperCase() + activeTask.stage.slice(1)

    // For transcribe with progress, show detailed progress
    if (activeTask.stage === 'transcribe' && progress) {
      const progressLabel = STAGE_LABELS[progress.stage] || progress.message
      const eta = progress.estimated_remaining_seconds
        ? formatTimeRemaining(progress.estimated_remaining_seconds)
        : null

      return (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-3 px-4 py-2 rounded-lg bg-purple-50 text-purple-700 border border-purple-200">
              <SpinnerIcon />
              <div className="flex flex-col">
                <span className="text-sm font-medium">
                  {stageLabel}: {progressLabel}
                </span>
                <div className="flex items-center gap-2 mt-1">
                  <ProgressBar percent={progress.progress_pct} />
                  <span className="text-xs text-purple-600">{progress.progress_pct}%</span>
                </div>
                {eta && (
                  <span className="text-xs text-purple-500 mt-0.5">{eta}</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )
    }

    // Default processing display (non-transcribe or no progress yet)
    // Distinguish between actively processing vs waiting in queue
    if (isProcessing) {
      return (
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-50 text-purple-700 border border-purple-200">
            <SpinnerIcon />
            <span className="text-sm font-medium">
              {stageLabel}: Processing...
            </span>
          </div>
        </div>
      )
    }

    // Task is queued but not yet being processed
    return (
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-50 text-gray-600 border border-gray-200">
          <QueueIcon />
          <span className="text-sm font-medium">
            {stageLabel}: Queued
          </span>
          <span className="text-xs text-gray-400">(waiting for worker)</span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        {/* Split button */}
        <div className="relative inline-flex" ref={dropdownRef}>
          {/* Main button - runs next step */}
          <button
            onClick={handleSingleStep}
            disabled={isDisabled}
            className={`
              inline-flex items-center gap-2 px-4 py-2 rounded-l-lg font-medium text-sm text-white
              transition-all duration-200 shadow-sm hover:shadow
              ${isDisabled ? 'bg-gray-400 cursor-not-allowed' : stageColors[action.stage]}
            `}
          >
            {isPending ? <SpinnerIcon /> : getIcon(action.icon)}
            <span>{action.label}</span>
          </button>

          {/* Dropdown trigger */}
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            disabled={isDisabled}
            className={`
              inline-flex items-center px-2 py-2 rounded-r-lg font-medium text-sm text-white
              border-l border-white/20
              transition-all duration-200 shadow-sm hover:shadow
              ${isDisabled ? 'bg-gray-400 cursor-not-allowed' : stageColors[action.stage]}
            `}
            aria-label="More options"
          >
            <ChevronDownIcon />
          </button>

          {/* Dropdown menu */}
          {dropdownOpen && !isDisabled && (
            <div className="absolute top-full left-0 mt-1 w-56 bg-white rounded-lg shadow-lg border border-gray-200 z-10">
              <div className="py-1">
                <button
                  onClick={handleSingleStep}
                  className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 flex items-center gap-2"
                >
                  {getIcon(action.icon)}
                  <span>{action.label} (next step)</span>
                </button>
                <button
                  onClick={handleFullPipeline}
                  className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 flex items-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                  </svg>
                  <span>Run Full Pipeline</span>
                  <span className="ml-auto text-xs text-gray-400">→ Ready</span>
                </button>
              </div>
            </div>
          )}
        </div>

        {recentFailedTask && !error && (
          <span className="text-sm text-red-600" title={recentFailedTask.error_message || undefined}>
            Last task failed
          </span>
        )}
      </div>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-md">
          {error}
        </div>
      )}
    </div>
  )
}
