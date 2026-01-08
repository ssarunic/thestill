import type { PipelineStage } from '../api/types'

// Pipeline stages configuration
const STAGES: { key: PipelineStage; label: string; icon: JSX.Element }[] = [
  {
    key: 'download',
    label: 'Download',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
      </svg>
    ),
  },
  {
    key: 'downsample',
    label: 'Downsample',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
      </svg>
    ),
  },
  {
    key: 'transcribe',
    label: 'Transcribe',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
      </svg>
    ),
  },
  {
    key: 'clean',
    label: 'Clean',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
      </svg>
    ),
  },
  {
    key: 'summarize',
    label: 'Summarize',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
]

// Human-readable labels for transcription sub-stages
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

// Progress update from SSE
interface ProgressUpdate {
  stage: string
  progress_pct: number
  message: string
  estimated_remaining_seconds: number | null
}

interface PipelineStepperProps {
  /** Current stage being processed (null if not started) */
  currentStage: PipelineStage | null
  /** The starting stage based on episode state */
  startingStage: PipelineStage
  /** Progress update for detailed status (optional) */
  progress?: ProgressUpdate | null
  /** Show compact version without labels */
  compact?: boolean
}

function CheckIcon() {
  return (
    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
    </svg>
  )
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

type StageStatus = 'completed' | 'current' | 'pending' | 'skipped'

export default function PipelineStepper({
  currentStage,
  startingStage,
  progress,
  compact = false,
}: PipelineStepperProps) {
  const stageKeys = STAGES.map((s) => s.key)
  const startIdx = stageKeys.indexOf(startingStage)
  const currentIdx = currentStage ? stageKeys.indexOf(currentStage) : -1

  const getStageStatus = (_stageKey: PipelineStage, idx: number): StageStatus => {
    // Stages before the starting stage are "skipped" (already done before pipeline started)
    if (idx < startIdx) return 'skipped'
    // Stages that have been processed in this pipeline run
    if (currentIdx >= 0 && idx < currentIdx) return 'completed'
    // The current stage being processed
    if (idx === currentIdx) return 'current'
    // Future stages
    return 'pending'
  }

  // Get progress info for the current stage
  const progressLabel = progress ? (STAGE_LABELS[progress.stage] || progress.message) : null
  const progressPct = progress?.progress_pct ?? 0
  const eta = progress?.estimated_remaining_seconds
    ? formatTimeRemaining(progress.estimated_remaining_seconds)
    : null

  return (
    <div className="flex flex-col gap-2">
      {/* Stepper */}
      <div className="flex items-center">
        {STAGES.map((stage, idx) => {
          const status = getStageStatus(stage.key, idx)
          const isLast = idx === STAGES.length - 1

          return (
            <div key={stage.key} className="flex items-center">
              {/* Stage circle */}
              <div className="flex flex-col items-center">
                <div
                  className={`
                    w-8 h-8 rounded-full flex items-center justify-center transition-all duration-300
                    ${status === 'completed' ? 'bg-green-500 text-white' : ''}
                    ${status === 'current' ? 'bg-purple-500 text-white ring-4 ring-purple-200' : ''}
                    ${status === 'pending' ? 'bg-gray-200 text-gray-400' : ''}
                    ${status === 'skipped' ? 'bg-green-100 text-green-600' : ''}
                  `}
                  title={`${stage.label}${status === 'completed' ? ' (completed)' : status === 'current' ? ' (in progress)' : status === 'skipped' ? ' (already done)' : ''}`}
                >
                  {status === 'completed' || status === 'skipped' ? (
                    <CheckIcon />
                  ) : status === 'current' ? (
                    <SpinnerIcon />
                  ) : (
                    stage.icon
                  )}
                </div>
                {/* Label (only in non-compact mode) */}
                {!compact && (
                  <span
                    className={`
                      text-xs mt-1 font-medium whitespace-nowrap
                      ${status === 'completed' ? 'text-green-600' : ''}
                      ${status === 'current' ? 'text-purple-600' : ''}
                      ${status === 'pending' ? 'text-gray-400' : ''}
                      ${status === 'skipped' ? 'text-green-500' : ''}
                    `}
                  >
                    {stage.label}
                  </span>
                )}
              </div>

              {/* Connector line */}
              {!isLast && (
                <div
                  className={`
                    w-6 h-1 mx-1 rounded transition-all duration-300
                    ${status === 'completed' || status === 'skipped' ? 'bg-green-400' : ''}
                    ${status === 'current' ? 'bg-gradient-to-r from-purple-400 to-gray-200' : ''}
                    ${status === 'pending' ? 'bg-gray-200' : ''}
                  `}
                  style={{ marginTop: compact ? 0 : '-1rem' }}
                />
              )}
            </div>
          )
        })}
      </div>

      {/* Current stage detail */}
      {currentStage && (
        <div className="text-sm text-gray-600 mt-1">
          <span className="font-medium text-purple-700">
            {STAGES.find((s) => s.key === currentStage)?.label}
          </span>
          {progressLabel && (
            <span className="text-gray-500">: {progressLabel}</span>
          )}
          {progressPct > 0 && (
            <span className="text-gray-400 ml-1">({progressPct}%)</span>
          )}
          {eta && (
            <span className="text-gray-400 ml-2">{eta}</span>
          )}
        </div>
      )}

      {/* Progress bar for transcription */}
      {progress && progressPct > 0 && (
        <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-purple-500 transition-all duration-300 ease-out"
            style={{ width: `${Math.min(100, Math.max(0, progressPct))}%` }}
          />
        </div>
      )}
    </div>
  )
}
