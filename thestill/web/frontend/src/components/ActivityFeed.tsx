import { useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import type { ActivityItem } from '../api/types'

interface ActivityFeedProps {
  items: ActivityItem[]
  isLoading?: boolean
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  fetchNextPage?: () => void
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

function ActionBadge({ action }: { action: string }) {
  const colors: Record<string, string> = {
    discovered: 'bg-gray-100 text-gray-600',
    downloaded: 'bg-yellow-100 text-yellow-700',
    downsampled: 'bg-orange-100 text-orange-700',
    transcribed: 'bg-purple-100 text-purple-700',
    cleaned: 'bg-blue-100 text-blue-700',
    summarized: 'bg-green-100 text-green-700',
  }

  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[action] || 'bg-gray-100 text-gray-700'}`}>
      {action}
    </span>
  )
}

export default function ActivityFeed({
  items,
  isLoading,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: ActivityFeedProps) {
  // Intersection Observer for infinite scroll
  const loadMoreRef = useRef<HTMLDivElement>(null)

  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const [entry] = entries
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage && fetchNextPage) {
        fetchNextPage()
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage]
  )

  useEffect(() => {
    const element = loadMoreRef.current
    if (!element || !fetchNextPage) return

    const observer = new IntersectionObserver(handleObserver, {
      root: null,
      rootMargin: '100px',
      threshold: 0,
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [handleObserver, fetchNextPage])

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="animate-pulse flex gap-4 p-4 bg-white rounded-lg">
            <div className="w-10 h-10 bg-gray-200 rounded-full" />
            <div className="flex-1 space-y-2">
              <div className="h-4 bg-gray-200 rounded w-3/4" />
              <div className="h-3 bg-gray-200 rounded w-1/2" />
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <svg className="w-12 h-12 mx-auto mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p>No recent activity</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <Link
          key={item.episode_id}
          to={`/episodes/${item.episode_id}`}
          className="flex items-start gap-4 p-4 bg-white rounded-lg border border-gray-100 hover:border-gray-200 hover:shadow-sm transition-all"
        >
          <div className="w-10 h-10 bg-secondary-100 rounded-full flex items-center justify-center flex-shrink-0">
            <svg className="w-5 h-5 text-secondary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="font-medium text-gray-900 truncate">{item.episode_title}</h4>
              <ActionBadge action={item.action} />
            </div>
            <p className="text-sm text-gray-500 truncate">{item.podcast_title}</p>
          </div>
          <div className="text-xs text-gray-400 flex-shrink-0">
            {formatTimestamp(item.timestamp)}
          </div>
        </Link>
      ))}

      {/* Load more trigger */}
      {fetchNextPage && (
        <div ref={loadMoreRef} className="py-4">
          {isFetchingNextPage && (
            <div className="flex justify-center">
              <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
            </div>
          )}
          {!hasNextPage && items.length > 0 && (
            <p className="text-center text-gray-400 text-sm">All activity loaded</p>
          )}
        </div>
      )}
    </div>
  )
}
