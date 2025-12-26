import { useRefreshStatus, useStartRefresh } from '../hooks/useApi'

export default function RefreshButton() {
  const { data: status } = useRefreshStatus()
  const { mutate: startRefresh, isPending } = useStartRefresh()

  const isRunning = status?.status === 'running'
  const isDisabled = isPending || isRunning

  const handleClick = () => {
    if (!isDisabled) {
      startRefresh({})
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handleClick}
        disabled={isDisabled}
        className={`
          inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm
          transition-all duration-200
          ${isDisabled
            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
            : 'bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm hover:shadow'
          }
        `}
      >
        {isRunning ? (
          <>
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
            <span>Refreshing...</span>
          </>
        ) : (
          <>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            <span>Refresh Feeds</span>
          </>
        )}
      </button>

      {/* Status indicator */}
      {isRunning && status && (
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-600 transition-all duration-300"
              style={{ width: `${status.progress}%` }}
            />
          </div>
          <span className="text-xs">{status.progress}%</span>
        </div>
      )}

      {/* Completed status */}
      {status?.status === 'completed' && status.result && (
        <span className="text-sm text-green-600">
          Found {status.result.total_episodes} new episode{status.result.total_episodes !== 1 ? 's' : ''}
        </span>
      )}

      {/* Failed status */}
      {status?.status === 'failed' && status.error && (
        <span className="text-sm text-red-600">
          Failed: {status.error}
        </span>
      )}
    </div>
  )
}
