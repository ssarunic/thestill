import { useDashboardStats, useRecentActivityInfinite } from '../hooks/useApi'
import StatusCard from '../components/StatusCard'
import ActivityFeed from '../components/ActivityFeed'
import PipelineStatus from '../components/PipelineStatus'

export default function Dashboard() {
  const { data: stats, isLoading: statsLoading, error: statsError } = useDashboardStats()
  const {
    data: activityData,
    isLoading: activityLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useRecentActivityInfinite(10)

  // Flatten all pages into a single items array
  const allActivityItems = activityData?.pages.flatMap((page) => page.items) ?? []

  if (statsError) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading dashboard</h2>
          <p className="text-red-600 text-sm">{statsError.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-1">Overview of your podcast processing pipeline</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatusCard
          label="Podcasts Tracked"
          value={statsLoading ? '...' : stats?.podcasts_tracked ?? 0}
          color="primary"
          icon={
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          }
        />
        <StatusCard
          label="Total Episodes"
          value={statsLoading ? '...' : stats?.episodes_total ?? 0}
          color="secondary"
          icon={
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
            </svg>
          }
        />
        <StatusCard
          label="Processed"
          value={statsLoading ? '...' : stats?.episodes_processed ?? 0}
          color="success"
          subtitle={stats ? `${Math.round((stats.episodes_processed / stats.episodes_total) * 100) || 0}% complete` : undefined}
          icon={
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
        />
        <StatusCard
          label="Pending"
          value={statsLoading ? '...' : stats?.episodes_pending ?? 0}
          color="warning"
          icon={
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
        />
      </div>

      {/* Pipeline Status */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Pipeline Status</h2>
        {statsLoading ? (
          <div className="animate-pulse h-20 bg-gray-100 rounded" />
        ) : stats ? (
          <PipelineStatus pipeline={stats.pipeline} />
        ) : null}
      </div>

      {/* Recent Activity */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Recent Activity</h2>
        <ActivityFeed
          items={allActivityItems}
          isLoading={activityLoading}
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
          fetchNextPage={fetchNextPage}
        />
      </div>
    </div>
  )
}
