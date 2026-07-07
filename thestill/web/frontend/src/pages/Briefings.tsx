import { Link } from 'react-router-dom'
import { useBriefingsInfinite } from '../hooks/useApi'
import type { Briefing } from '../api/types'

// Past-briefings history (digest retirement follow-up). Deliberately a
// lightweight list reached from the inbox card — the briefing's home
// surface stays the inbox (spec #36 decision log); this page only serves
// "what did earlier briefings cover".

function formatCreatedAt(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatWindow(briefing: Briefing): string {
  const from = new Date(briefing.cursor_from)
  const to = new Date(briefing.cursor_to)
  const fmt = (d: Date) =>
    d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  // The epoch lower bound marks a first-run briefing covering the whole inbox.
  if (from.getFullYear() <= 1970) return `everything up to ${fmt(to)}`
  return `${fmt(from)} – ${fmt(to)}`
}

function BriefingRow({ briefing }: { briefing: Briefing }) {
  return (
    <Link
      to={`/briefings/${briefing.id}`}
      className="group flex items-center gap-4 p-4 bg-white border border-gray-200 rounded-lg hover:border-primary-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 transition-all"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900 group-hover:text-primary-700">
          {formatCreatedAt(briefing.created_at)}
        </p>
        <p className="text-sm text-gray-500">
          {briefing.episode_count} episode{briefing.episode_count === 1 ? '' : 's'}
          {' • '}
          covers {formatWindow(briefing)}
        </p>
      </div>
      {briefing.listened_at ? (
        <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5">
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          Listened
        </span>
      ) : (
        <span className="text-xs font-medium text-gray-400">Unplayed</span>
      )}
      <span className="text-sm font-medium text-primary-700 group-hover:text-primary-800">
        Read →
      </span>
    </Link>
  )
}

export default function Briefings() {
  const query = useBriefingsInfinite(20)

  const briefings = query.data?.pages.flatMap((page) => page.briefings) ?? []
  const total = query.data?.pages[0]?.total ?? 0

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Past briefings</h1>
        <p className="text-sm text-gray-500 mt-1">
          {total > 0
            ? `${total} briefing${total === 1 ? '' : 's'} — newest first`
            : 'Every briefing you generate is kept here.'}
        </p>
      </div>

      {query.isLoading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="animate-pulse h-16 bg-white border border-gray-200 rounded-lg" />
          ))}
        </div>
      )}

      {query.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          Couldn't load briefings: {(query.error as Error).message}
        </div>
      )}

      {!query.isLoading && !query.error && briefings.length === 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-500">
          <p className="font-medium text-gray-700 mb-1">No briefings yet</p>
          <p className="text-sm">
            Your first briefing is generated when new episodes land in your{' '}
            <Link to="/inbox" className="text-primary-600 hover:underline">
              inbox
            </Link>
            {' '}— or on your schedule, configured in{' '}
            <Link to="/settings" className="text-primary-600 hover:underline">
              Settings
            </Link>
            .
          </p>
        </div>
      )}

      <div className="space-y-3">
        {briefings.map((briefing) => (
          <BriefingRow key={briefing.id} briefing={briefing} />
        ))}
      </div>

      {query.hasNextPage && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
            className="px-4 py-2 text-sm font-medium text-primary-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
          >
            {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  )
}
