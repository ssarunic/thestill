import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import type { FailureType } from '../api/types'

interface FailureDetailsModalProps {
  isOpen: boolean
  onClose: () => void
  episodeTitle: string
  episodeSlug?: string
  podcastSlug?: string
  podcastTitle?: string
  failedAtStage: string
  failureReason: string | null
  failureType: FailureType | null
  failedAt: string | null
  retryCount?: number
  maxRetries?: number
  onRetry?: () => void
  isRetrying?: boolean
}

// Stage labels
const stageLabels: Record<string, string> = {
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcription',
  clean: 'Cleaning',
  summarize: 'Summary',
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown time'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function FailureDetailsModal({
  isOpen,
  onClose,
  episodeTitle,
  episodeSlug,
  podcastSlug,
  podcastTitle,
  failedAtStage,
  failureReason,
  failureType,
  failedAt,
  retryCount,
  maxRetries,
  onRetry,
  isRetrying,
}: FailureDetailsModalProps) {
  // Handle escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    if (isOpen) {
      document.addEventListener('keydown', handleEscape)
      document.body.style.overflow = 'hidden'
    }
    return () => {
      document.removeEventListener('keydown', handleEscape)
      document.body.style.overflow = ''
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  const isFatal = failureType === 'fatal'

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black bg-opacity-50 transition-opacity"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          className="relative bg-white rounded-lg shadow-xl max-w-lg w-full max-h-[90vh] overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className={`px-6 py-4 border-b ${isFatal ? 'bg-red-50 border-red-200' : 'bg-yellow-50 border-yellow-200'}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className={`flex-shrink-0 ${isFatal ? 'text-red-500' : 'text-yellow-500'}`}>
                  <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                      clipRule="evenodd"
                    />
                  </svg>
                </div>
                <div>
                  <h3 className={`font-semibold ${isFatal ? 'text-red-800' : 'text-yellow-800'}`}>
                    {isFatal ? 'Fatal Error' : 'Transient Error'}
                  </h3>
                  <p className={`text-sm ${isFatal ? 'text-red-600' : 'text-yellow-600'}`}>
                    Processing failed at {stageLabels[failedAtStage] || failedAtStage} stage
                  </p>
                </div>
              </div>
              <button
                onClick={onClose}
                className="text-gray-400 hover:text-gray-500 transition-colors"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>

          {/* Content */}
          <div className="px-6 py-4 overflow-y-auto max-h-[60vh]">
            {/* Episode info */}
            <div className="mb-4">
              <h4 className="text-sm font-medium text-gray-500 mb-1">Episode</h4>
              {episodeSlug && podcastSlug ? (
                <Link
                  to={`/podcasts/${podcastSlug}/episodes/${episodeSlug}`}
                  className="text-gray-900 font-medium hover:text-primary-600"
                  onClick={onClose}
                >
                  {episodeTitle}
                </Link>
              ) : (
                <p className="text-gray-900 font-medium">{episodeTitle}</p>
              )}
              {podcastTitle && (
                <p className="text-sm text-gray-500">{podcastTitle}</p>
              )}
            </div>

            {/* Failure details */}
            <div className="space-y-4">
              {/* Timestamp */}
              {failedAt && (
                <div>
                  <h4 className="text-sm font-medium text-gray-500 mb-1">Failed At</h4>
                  <p className="text-gray-900">{formatDate(failedAt)}</p>
                </div>
              )}

              {/* Retry info */}
              {retryCount !== undefined && maxRetries !== undefined && (
                <div>
                  <h4 className="text-sm font-medium text-gray-500 mb-1">Retry Attempts</h4>
                  <p className="text-gray-900">{retryCount} of {maxRetries}</p>
                </div>
              )}

              {/* Error message */}
              {failureReason && (
                <div>
                  <h4 className="text-sm font-medium text-gray-500 mb-1">Error Details</h4>
                  <pre
                    className={`p-3 rounded-lg text-sm overflow-x-auto whitespace-pre-wrap break-words ${
                      isFatal ? 'bg-red-50 text-red-800' : 'bg-yellow-50 text-yellow-800'
                    }`}
                  >
                    {failureReason}
                  </pre>
                </div>
              )}

              {/* Help text */}
              <div className={`p-3 rounded-lg text-sm ${isFatal ? 'bg-red-50' : 'bg-yellow-50'}`}>
                <p className={isFatal ? 'text-red-700' : 'text-yellow-700'}>
                  {isFatal
                    ? 'This is a permanent error that requires investigation. Check the error details above and fix the underlying issue before retrying.'
                    : 'This is a temporary error that may succeed on retry. Common causes include network issues, rate limits, or temporary service unavailability.'}
                </p>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
            >
              Close
            </button>
            {onRetry && (
              <button
                onClick={onRetry}
                disabled={isRetrying}
                className={`
                  px-4 py-2 text-sm font-medium rounded-lg transition-colors
                  ${isRetrying
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : isFatal
                      ? 'bg-red-600 text-white hover:bg-red-700'
                      : 'bg-yellow-600 text-white hover:bg-yellow-700'
                  }
                `}
              >
                {isRetrying ? 'Retrying...' : 'Retry'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
