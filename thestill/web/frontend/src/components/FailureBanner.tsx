import { useState } from 'react'
import { useRetryFailedEpisode } from '../hooks/useApi'
import type { FailureType } from '../api/types'

interface FailureBannerProps {
  episodeId: string
  failedAtStage: string
  failureReason: string | null
  failureType: FailureType | null
  failedAt: string | null
  onRetrySuccess?: () => void
}

// Format date for display
function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown time'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Stage labels
const stageLabels: Record<string, string> = {
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcription',
  clean: 'Cleaning',
  summarize: 'Summary',
}

export default function FailureBanner({
  episodeId,
  failedAtStage,
  failureReason,
  failureType,
  failedAt,
  onRetrySuccess,
}: FailureBannerProps) {
  const [showDetails, setShowDetails] = useState(false)
  const retryMutation = useRetryFailedEpisode()

  const handleRetry = async () => {
    try {
      await retryMutation.mutateAsync(episodeId)
      onRetrySuccess?.()
    } catch (error) {
      // Error handling is managed by the mutation
    }
  }

  const isFatal = failureType === 'fatal'

  return (
    <div
      className={`rounded-lg border p-4 ${
        isFatal
          ? 'bg-red-50 border-red-200'
          : 'bg-yellow-50 border-yellow-200'
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className={`flex-shrink-0 ${isFatal ? 'text-red-500' : 'text-yellow-500'}`}>
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
            <path
              fillRule="evenodd"
              d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
              clipRule="evenodd"
            />
          </svg>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div>
              <h3 className={`font-medium ${isFatal ? 'text-red-800' : 'text-yellow-800'}`}>
                {isFatal ? 'Processing failed (fatal error)' : 'Processing failed (temporary error)'}
              </h3>
              <p className={`text-sm mt-1 ${isFatal ? 'text-red-700' : 'text-yellow-700'}`}>
                Failed at <span className="font-medium">{stageLabels[failedAtStage] || failedAtStage}</span> stage
                {failedAt && <> on {formatDate(failedAt)}</>}
              </p>
            </div>

            {/* Retry button */}
            <button
              onClick={handleRetry}
              disabled={retryMutation.isPending}
              className={`
                px-4 py-2 text-sm font-medium rounded-lg transition-colors flex-shrink-0
                ${retryMutation.isPending
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : isFatal
                    ? 'bg-red-600 text-white hover:bg-red-700'
                    : 'bg-yellow-600 text-white hover:bg-yellow-700'
                }
              `}
            >
              {retryMutation.isPending ? 'Retrying...' : 'Retry'}
            </button>
          </div>

          {/* Error details toggle */}
          {failureReason && (
            <div className="mt-3">
              <button
                onClick={() => setShowDetails(!showDetails)}
                className={`text-xs flex items-center gap-1 ${
                  isFatal ? 'text-red-600 hover:text-red-700' : 'text-yellow-600 hover:text-yellow-700'
                }`}
              >
                <svg
                  className={`w-3 h-3 transition-transform ${showDetails ? 'rotate-90' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                {showDetails ? 'Hide error details' : 'Show error details'}
              </button>

              {showDetails && (
                <pre
                  className={`mt-2 p-3 rounded text-xs overflow-x-auto whitespace-pre-wrap break-words ${
                    isFatal ? 'bg-red-100 text-red-800' : 'bg-yellow-100 text-yellow-800'
                  }`}
                >
                  {failureReason}
                </pre>
              )}
            </div>
          )}

          {/* Help text */}
          <p className={`text-xs mt-3 ${isFatal ? 'text-red-600' : 'text-yellow-600'}`}>
            {isFatal
              ? 'This error requires investigation. Check the error details and try again after fixing the issue.'
              : 'This may be a temporary issue. Retrying might resolve the problem.'}
          </p>

          {/* Mutation error */}
          {retryMutation.error && (
            <p className="text-sm text-red-600 mt-2">
              Retry failed: {retryMutation.error.message}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
