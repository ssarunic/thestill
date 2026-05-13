import { Link } from 'react-router-dom'
import { useLatestDigest } from '../hooks/useApi'

function formatRelative(iso: string): string {
  const created = new Date(iso).getTime()
  const elapsedMin = (Date.now() - created) / 60000
  if (elapsedMin < 1) return 'just now'
  if (elapsedMin < 60) return `${Math.round(elapsedMin)} min ago`
  const hours = elapsedMin / 60
  if (hours < 24) return `${Math.round(hours)} hr ago`
  const days = hours / 24
  return `${Math.round(days)} day${days >= 1.5 ? 's' : ''} ago`
}

// "Today's briefing" card at the top of /inbox. The card shows the
// user's most recent inbox-driven digest; `GET /api/digests/latest`
// lazy-generates one if the throttle has elapsed and there are
// eligible inbox items in the window.
export default function BriefingCard() {
  const { data, isLoading, error } = useLatestDigest()

  if (isLoading) {
    return (
      <div className="animate-pulse h-20 bg-white border border-gray-200 rounded-lg" />
    )
  }

  // 404 means "nothing eligible to brief about" — a normal empty state,
  // not a UI error. Hide the card silently.
  if (error || !data?.digest) return null
  const digest = data.digest

  return (
    <Link
      to={`/digests/${digest.id}`}
      className="group flex items-center gap-4 p-4 bg-gradient-to-br from-primary-50 to-white border border-primary-200 rounded-lg hover:border-primary-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 transition-all"
    >
      <div className="w-10 h-10 rounded-full bg-primary-100 flex items-center justify-center flex-shrink-0">
        <svg
          className="w-5 h-5 text-primary-700"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
        </svg>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-base font-semibold text-gray-900 group-hover:text-primary-700">
          Today's briefing
        </p>
        <p className="text-sm text-gray-500">
          {digest.episodes_total} episode{digest.episodes_total === 1 ? '' : 's'}
          {' • '}
          generated {formatRelative(digest.created_at)}
        </p>
      </div>
      <span className="text-sm font-medium text-primary-700 group-hover:text-primary-800">
        Read →
      </span>
    </Link>
  )
}
