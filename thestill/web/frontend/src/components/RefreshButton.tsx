import { useEffect, useRef } from 'react'
import { useRefreshStatus, useStartRefresh } from '../hooks/useApi'
import { useToast } from './Toast'

export default function RefreshButton() {
  const { data: status } = useRefreshStatus()
  const { mutate: startRefresh, isPending } = useStartRefresh()
  const { showToast } = useToast()
  const prevStatusRef = useRef<string | undefined>()

  const isRunning = status?.status === 'running'
  const isDisabled = isPending || isRunning

  // Show toast when status changes to completed or failed
  useEffect(() => {
    const prevStatus = prevStatusRef.current
    const currentStatus = status?.status

    // Only show toast on status transition (not on initial load)
    if (prevStatus && prevStatus !== currentStatus) {
      if (currentStatus === 'completed' && status?.result) {
        const count = status.result.total_episodes
        showToast(
          `Found ${count} new episode${count !== 1 ? 's' : ''}`,
          'success'
        )
      } else if (currentStatus === 'failed' && status?.error) {
        showToast(`Refresh failed: ${status.error}`, 'error')
      }
    }

    prevStatusRef.current = currentStatus
  }, [status, showToast])

  const handleClick = () => {
    if (!isDisabled) {
      startRefresh({})
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={isDisabled}
      aria-label={isRunning ? 'Refreshing feeds' : 'Refresh feeds'}
      className={`
        inline-flex items-center justify-center gap-2 min-w-[44px] min-h-[44px] px-3 sm:px-4 rounded-lg font-medium text-sm
        transition-all duration-200
        ${isDisabled
          ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
          : 'bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm hover:shadow'
        }
      `}
    >
      {isRunning ? (
        <>
          <svg className="w-5 h-5 sm:w-4 sm:h-4 animate-spin" fill="none" viewBox="0 0 24 24">
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
          <span className="hidden sm:inline">Refreshing...</span>
        </>
      ) : (
        <>
          <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          <span className="hidden sm:inline">Refresh Feeds</span>
        </>
      )}
    </button>
  )
}
