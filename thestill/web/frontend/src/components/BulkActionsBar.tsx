import { useBulkProcess } from '../hooks/useApi'

interface BulkActionsBarProps {
  selectedIds: Set<string>
  onClearSelection: () => void
}

export default function BulkActionsBar({ selectedIds, onClearSelection }: BulkActionsBarProps) {
  const bulkProcess = useBulkProcess()

  const handleProcessAll = async () => {
    const episodeIds = Array.from(selectedIds)
    try {
      const result = await bulkProcess.mutateAsync(episodeIds)

      // Show result notification (basic alert for now)
      if (result.queued > 0 || result.skipped > 0) {
        const message = []
        if (result.queued > 0) {
          message.push(`Queued ${result.queued} episode${result.queued === 1 ? '' : 's'}`)
        }
        if (result.skipped > 0) {
          message.push(`Skipped ${result.skipped} (already complete or not found)`)
        }
        alert(message.join(', '))
      }

      // Clear selection after successful processing
      onClearSelection()
    } catch (error) {
      alert(`Error: ${error instanceof Error ? error.message : 'Failed to process episodes'}`)
    }
  }

  if (selectedIds.size === 0) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 shadow-lg z-40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <span className="text-sm font-medium text-gray-700">
              {selectedIds.size} episode{selectedIds.size === 1 ? '' : 's'} selected
            </span>
            <button
              onClick={onClearSelection}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Clear selection
            </button>
          </div>

          <div className="flex items-center gap-3">
            {bulkProcess.isPending && (
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-600"></div>
                Processing...
              </div>
            )}

            <button
              onClick={handleProcessAll}
              disabled={bulkProcess.isPending}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg font-medium text-sm hover:bg-indigo-700 active:bg-indigo-800 transition-all duration-200 shadow-sm hover:shadow disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"
                />
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              Process All
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
