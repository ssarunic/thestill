import { useState, useEffect, useRef } from 'react'
import { useQueuePipelineTask, useEpisodeTasks } from '../hooks/useApi'
import type { PipelineStage } from '../api/types'

interface PipelineActionButtonProps {
  podcastSlug: string
  episodeSlug: string
  episodeId: string
  episodeState: string
  onTaskComplete?: (stage: PipelineStage) => void
}

// Map episode state to the next action
const stateToAction: Record<string, { stage: PipelineStage; label: string; icon: string }> = {
  discovered: { stage: 'download', label: 'Download', icon: 'download' },
  downloaded: { stage: 'downsample', label: 'Downsample', icon: 'waveform' },
  downsampled: { stage: 'transcribe', label: 'Transcribe', icon: 'mic' },
  transcribed: { stage: 'clean', label: 'Clean', icon: 'sparkles' },
  cleaned: { stage: 'summarize', label: 'Summarize', icon: 'document' },
}

// Color scheme for different states
const stageColors: Record<PipelineStage, string> = {
  download: 'bg-blue-600 hover:bg-blue-700',
  downsample: 'bg-indigo-600 hover:bg-indigo-700',
  transcribe: 'bg-purple-600 hover:bg-purple-700',
  clean: 'bg-amber-600 hover:bg-amber-700',
  summarize: 'bg-green-600 hover:bg-green-700',
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

export default function PipelineActionButton({
  podcastSlug,
  episodeSlug,
  episodeId,
  episodeState,
  onTaskComplete,
}: PipelineActionButtonProps) {
  const [error, setError] = useState<string | null>(null)
  const { mutate: queueTask, isPending } = useQueuePipelineTask(podcastSlug, episodeSlug)
  const { data: tasksData } = useEpisodeTasks(episodeId)

  // Track previous active task to detect completion
  const prevActiveTaskRef = useRef<{ id: string; stage: PipelineStage } | null>(null)

  // Get action for current state
  const action = stateToAction[episodeState]

  // Check if there's already an active task
  const activeTask = tasksData?.tasks?.find(
    (t) => t.status === 'pending' || t.status === 'processing'
  )

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

  // If already summarized, don't show any action
  if (episodeState === 'summarized') {
    return (
      <div className="flex items-center gap-2 text-green-600">
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
        <span className="text-sm font-medium">Complete</span>
      </div>
    )
  }

  // If no action available, return null
  if (!action) {
    return null
  }

  const handleClick = () => {
    setError(null)
    queueTask(action.stage, {
      onError: (err: Error) => {
        setError(err.message)
      },
    })
  }

  const isDisabled = isPending || !!activeTask
  const isProcessing = activeTask?.status === 'processing'

  // Show processing/pending status
  if (activeTask) {
    const statusLabel = isProcessing ? 'Processing...' : 'Queued...'
    const stageLabel = activeTask.stage.charAt(0).toUpperCase() + activeTask.stage.slice(1)

    return (
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-100 text-gray-600">
          <SpinnerIcon />
          <span className="text-sm font-medium">
            {stageLabel}: {statusLabel}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <button
          onClick={handleClick}
          disabled={isDisabled}
          className={`
            inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm text-white
            transition-all duration-200 shadow-sm hover:shadow
            ${isDisabled ? 'bg-gray-400 cursor-not-allowed' : stageColors[action.stage]}
          `}
        >
          {isPending ? <SpinnerIcon /> : getIcon(action.icon)}
          <span>{action.label}</span>
        </button>

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
