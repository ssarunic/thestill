import { Link } from 'react-router-dom'
import { useInbox } from '../hooks/useApi'
import type { InboxItem, InboxState } from '../api/types'

function formatDelivered(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

const STATE_TONE: Record<InboxState, string> = {
  unread: 'bg-primary-100 text-primary-700',
  saved: 'bg-yellow-100 text-yellow-800',
  dismissed: 'bg-gray-100 text-gray-500',
  read: 'bg-gray-100 text-gray-600',
}

function StateBadge({ state }: { state: InboxState }) {
  return (
    <span
      className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded ${STATE_TONE[state]}`}
    >
      {state}
    </span>
  )
}

function InboxRow({ item }: { item: InboxItem }) {
  const { entry, episode, podcast } = item
  const episodeHref = `/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`
  return (
    <li>
      <Link
        to={episodeHref}
        className="group flex items-start gap-4 p-4 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 hover:border-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 transition-colors"
      >
        {podcast.image_url ? (
          <img
            src={podcast.image_url}
            alt=""
            className="w-12 h-12 rounded object-cover flex-shrink-0"
          />
        ) : (
          <div className="w-12 h-12 rounded bg-gray-100 flex-shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2">
            <p className="text-sm text-gray-500 truncate">{podcast.title}</p>
            <span className="text-gray-300">·</span>
            <p className="text-xs text-gray-400">{formatDelivered(entry.delivered_at)}</p>
          </div>
          <p className="text-base font-medium text-gray-900 group-hover:text-primary-600 truncate">
            {episode.title}
          </p>
        </div>
        <StateBadge state={entry.state} />
      </Link>
    </li>
  )
}

export default function Inbox() {
  const { data, isLoading, error } = useInbox()

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading inbox</h2>
          <p className="text-red-600 text-sm">{error.message}</p>
        </div>
      </div>
    )
  }

  const items = data?.items ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Inbox</h1>
        <p className="text-gray-500 mt-1">
          {isLoading ? 'Loading…' : `${items.length} delivered`}
        </p>
      </div>

      {isLoading ? (
        <ul className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <li key={i} className="animate-pulse h-20 bg-white border border-gray-200 rounded-lg" />
          ))}
        </ul>
      ) : items.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <h3 className="text-lg font-medium text-gray-900 mb-2">No deliveries yet</h3>
          <p className="text-gray-500">
            Follow a podcast to receive new episodes in your inbox.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <InboxRow key={item.entry.id} item={item} />
          ))}
        </ul>
      )}
    </div>
  )
}
