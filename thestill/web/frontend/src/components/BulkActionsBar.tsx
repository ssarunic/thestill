import { useAuth } from '../contexts/AuthContext'
import Button from './Button'
import { abovePlayer } from '../constants/layers'
import { useBulkProcess } from '../hooks/useApi'

interface BulkActionsBarProps {
  selectedIds: Set<string>
  onClearSelection: () => void
}

export default function BulkActionsBar({ selectedIds, onClearSelection }: BulkActionsBarProps) {
  // Bulk processing mirrors the server-side require_admin gate on
  // POST /api/episodes/bulk/process (always satisfied in single-user mode).
  const { isAdmin } = useAuth()
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

  if (!isAdmin || selectedIds.size === 0) return null

  return (
    <div
      className="fixed left-0 right-0 bg-white border-t border-gray-200 shadow-lg z-40"
      style={{ bottom: abovePlayer() }}
    >
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
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary-600"></div>
                Processing...
              </div>
            )}

            <Button
              onClick={handleProcessAll}
              disabled={bulkProcess.isPending}
              icon={
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
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
              }
            >
              Process All
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
