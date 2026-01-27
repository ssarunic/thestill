import { Link } from 'react-router-dom'
import { useLatestDigest, useCreateMorningBriefing, useMorningBriefingCount } from '../hooks/useApi'
import { useToast } from './Toast'

interface MorningBriefingWidgetProps {
  className?: string
}

export default function MorningBriefingWidget({ className = '' }: MorningBriefingWidgetProps) {
  const { data: previewData, isLoading: previewLoading } = useMorningBriefingCount()
  const { data: latestDigestData } = useLatestDigest()
  const createMorningBriefingMutation = useCreateMorningBriefing()
  const { showToast } = useToast()

  const availableCount = previewData?.episodes?.length ?? 0
  const latestDigest = latestDigestData?.digest

  const handleQuickCatchUp = async () => {
    try {
      const result = await createMorningBriefingMutation.mutateAsync()

      if (result.status === 'completed') {
        showToast(`Digest created with ${result.episodes_selected} episodes`, 'success')
      } else if (result.status === 'no_episodes') {
        showToast('No new episodes to digest', 'info')
      } else {
        showToast(`Digest queued with ${result.episodes_selected} episodes`, 'success')
      }
    } catch (error) {
      showToast('Failed to create digest', 'error')
    }
  }

  // Status badge colors
  const statusColors: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-700',
    in_progress: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    partial: 'bg-orange-100 text-orange-700',
    failed: 'bg-red-100 text-red-700',
  }

  return (
    <div className={`bg-gradient-to-br from-indigo-50 to-purple-50 rounded-lg border border-indigo-200 p-4 sm:p-6 ${className}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
            <h3 className="text-sm font-semibold text-indigo-900">Morning Briefing</h3>
          </div>

          <p className="text-2xl sm:text-3xl font-bold text-indigo-700 mt-2">
            {previewLoading ? '...' : availableCount}
          </p>
          <p className="text-xs sm:text-sm text-indigo-600/70 mt-1">
            {availableCount === 1 ? 'episode' : 'episodes'} ready for digest
          </p>

          {latestDigest && (
            <div className="mt-3 flex items-center gap-2">
              <span className="text-xs text-indigo-600/60">Latest:</span>
              <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${statusColors[latestDigest.status]}`}>
                {latestDigest.status}
              </span>
              <span className="text-xs text-indigo-600/60">
                ({latestDigest.episodes_completed}/{latestDigest.episodes_total})
              </span>
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <button
            onClick={handleQuickCatchUp}
            disabled={createMorningBriefingMutation.isPending || availableCount === 0}
            className="
              px-3 py-2 text-sm font-medium rounded-lg
              bg-indigo-600 text-white
              hover:bg-indigo-700
              disabled:bg-indigo-400 disabled:cursor-not-allowed
              transition-colors
              flex items-center gap-2
            "
          >
            {createMorningBriefingMutation.isPending ? (
              <>
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                <span className="hidden sm:inline">Creating...</span>
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                <span className="hidden sm:inline">Quick Catch-Up</span>
              </>
            )}
          </button>

          <Link
            to="/digests"
            className="
              px-3 py-2 text-sm font-medium rounded-lg text-center
              text-indigo-600 bg-white border border-indigo-200
              hover:bg-indigo-50
              transition-colors
            "
          >
            <span className="hidden sm:inline">View All</span>
            <span className="sm:hidden">Digests</span>
          </Link>
        </div>
      </div>
    </div>
  )
}
