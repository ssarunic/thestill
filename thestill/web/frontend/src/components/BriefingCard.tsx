import { Link } from 'react-router-dom'
import { useGenerateBriefingNow, useLatestBriefing } from '../hooks/useApi'

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

// Spec #84: when the server schedules editions, the card says when the
// next one lands instead of pretending the open triggered anything.
function formatNextRun(iso: string): string {
  const next = new Date(iso)
  const now = new Date()
  const time = next.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const sameDay = next.toDateString() === now.toDateString()
  if (sameDay) return `next at ${time}`
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  if (next.toDateString() === tomorrow.toDateString()) return `next tomorrow ${time}`
  const day = next.toLocaleDateString(undefined, { weekday: 'short' })
  return `next ${day} ${time}`
}

// Spec #36: a "Today's briefing" card sits at the top of /inbox when a
// recent briefing exists for the current user. Clicking it opens the
// briefing detail page. On the lazy path the generation happens on the
// `/api/briefings/latest` GET inside `useLatestBriefing`; with the
// scheduler running (spec #84) that GET is a read and `next_run_at` says
// when the next edition is cut.
export default function BriefingCard() {
  const { data, isLoading, error } = useLatestBriefing()
  const generateNow = useGenerateBriefingNow()

  if (isLoading) {
    return (
      <div className="animate-pulse h-20 bg-white border border-gray-200 rounded-lg" />
    )
  }

  // 404 means "nothing eligible to brief about" — a normal empty state,
  // not a UI error. Hide the card silently but keep the history link so
  // past briefings stay reachable on quiet days.
  if (error || !data) {
    return (
      <div className="flex justify-end">
        <PastBriefingsLink />
      </div>
    )
  }

  if ('briefing_pending' in data) {
    const count = data.briefing_pending.pending_count
    return (
      <div className="space-y-1">
        <div
          role="status"
          className="flex flex-col sm:flex-row sm:items-center gap-3 p-4 bg-amber-50 border border-amber-200 rounded-lg"
        >
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-amber-950">Your briefing is catching up</p>
            <p className="text-sm text-amber-800">
              {count} episode{count === 1 ? '' : 's'} still processing
            </p>
          </div>
          <button
            type="button"
            onClick={() => generateNow.mutate()}
            disabled={generateNow.isPending}
            className="self-start sm:self-auto px-3 py-2 text-sm font-medium text-amber-950 bg-white border border-amber-300 rounded-md hover:bg-amber-100 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {generateNow.isPending ? 'Generating…' : 'Generate now'}
          </button>
        </div>
        {generateNow.isError && (
          <p role="alert" className="text-sm text-red-700">
            {generateNow.error.message}
          </p>
        )}
        <div className="flex justify-end">
          <PastBriefingsLink />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-1">
    <Link
      to={`/briefings/${data.id}`}
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
          {data.episode_count} episode{data.episode_count === 1 ? '' : 's'}
          {' • '}
          generated {formatRelative(data.created_at)}
          {data.listened_at ? ' • listened' : ''}
          {data.next_run_at ? ` • ${formatNextRun(data.next_run_at)}` : ''}
        </p>
      </div>
      <span className="text-sm font-medium text-primary-700 group-hover:text-primary-800">
        Read →
      </span>
    </Link>
      {generateNow.isError && (
        <p role="alert" className="text-sm text-red-700">
          {generateNow.error.message}
        </p>
      )}
      <div className="flex justify-end gap-4">
        {data.next_run_at && (
          <button
            type="button"
            onClick={() => generateNow.mutate()}
            disabled={generateNow.isPending}
            className="text-xs text-gray-500 hover:text-primary-700 hover:underline disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {generateNow.isPending ? 'Generating…' : 'Generate now'}
          </button>
        )}
        <PastBriefingsLink />
      </div>
    </div>
  )
}

function PastBriefingsLink() {
  return (
    <Link
      to="/briefings"
      className="text-xs text-gray-500 hover:text-primary-700 hover:underline"
    >
      Past briefings →
    </Link>
  )
}
