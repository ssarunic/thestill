import { useEffect, useRef, useCallback } from 'react'
import type { ActivityItem } from '../api/types'
import ListGroup from './ListGroup'
import ListRow, { ListRowArtwork } from './ListRow'

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

function formatPubDate(pubDate: string | null): string | null {
  if (!pubDate) return null
  const date = new Date(pubDate)
  if (Number.isNaN(date.getTime())) return null
  const now = new Date()
  const sameYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

function formatDuration(durationFormatted: string | null): string | null {
  if (!durationFormatted) return null
  // Convert "1:24:32" to "1h 24m" or "24:32" to "24m"
  const parts = durationFormatted.split(':')
  if (parts.length === 3) {
    // Hours:Minutes:Seconds
    const hours = parseInt(parts[0], 10)
    const minutes = parseInt(parts[1], 10)
    if (hours > 0) {
      return `${hours}h ${minutes}m`
    }
    return `${minutes}m`
  } else if (parts.length === 2) {
    // Minutes:Seconds
    const minutes = parseInt(parts[0], 10)
    return `${minutes}m`
  }
  return durationFormatted
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
      <ListGroup>
        {[...Array(5)].map((_, i) => (
          <li key={i} className="animate-pulse flex gap-3 px-4 py-3">
            <div className="w-12 h-12 bg-gray-200 rounded-md" />
            <div className="flex-1 space-y-2">
              <div className="h-4 bg-gray-200 rounded w-3/4" />
              <div className="h-3 bg-gray-200 rounded w-1/2" />
            </div>
          </li>
        ))}
      </ListGroup>
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
    <div>
      <ListGroup>
        {items.map((item) => (
          <ListRow
            key={item.episode_id}
            align="start"
            to={`/podcasts/${item.podcast_slug}/episodes/${item.episode_slug}`}
            leading={
              <ListRowArtwork sources={[item.episode_image_url, item.podcast_image_url]} />
            }
            overline={
              <div className="flex items-center gap-2">
                <ActionBadge action={item.action} />
                <span className="ml-auto text-xs text-gray-400 whitespace-nowrap">
                  {formatTimestamp(item.timestamp)}
                </span>
              </div>
            }
            title={item.episode_title}
            subtitle={
              <>
                {item.podcast_title}
                {formatPubDate(item.pub_date) && (
                  <span className="text-gray-400"> · {formatPubDate(item.pub_date)}</span>
                )}
                {item.duration_formatted && (
                  <span className="text-gray-400"> · {formatDuration(item.duration_formatted)}</span>
                )}
              </>
            }
          />
        ))}
      </ListGroup>

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
