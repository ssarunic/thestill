import { Link } from 'react-router-dom'
import type { Briefing, BriefingUpcoming } from '../api/types'
import { useGenerateBriefingNow, useLatestBriefing } from '../hooks/useApi'
import { editionTitle, formatUpcomingSlot } from '../utils/briefingFormat'

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

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

  const upcoming = data.upcoming
  const scheduled = Boolean(upcoming?.next_run_at ?? data.next_run_at)
  const showGenerate =
    scheduled || (upcoming !== undefined && upcoming.new_episode_count + upcoming.pending_count > 0)
  const listened = Boolean(data.listened_at)

  return (
    <div className="space-y-1">
      {listened ? (
        <Link
          to={`/briefings/${data.id}`}
          className="group flex items-center gap-3 px-4 py-2 bg-white border border-gray-200 rounded-lg hover:border-primary-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 transition-all"
        >
          <span aria-hidden="true" className="text-primary-600">✓</span>
          <p className="flex-1 min-w-0 text-sm text-gray-700 truncate">
            <span className="font-medium group-hover:text-primary-700">{editionTitle(data.created_at)}</span>
            {' • '}
            {plural(data.episode_count, 'episode')}
            {' • listened'}
          </p>
          <span className="text-xs font-medium text-primary-700">Open →</span>
        </Link>
      ) : (
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
              {editionTitle(data.created_at)}
            </p>
            <p className="text-sm text-gray-500">
              {plural(data.episode_count, 'episode')}
              {' • '}
              {new Date(data.created_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
            </p>
          </div>
          <span className="text-sm font-medium text-primary-700 group-hover:text-primary-800">
            Read →
          </span>
        </Link>
      )}
      {upcoming && (
        <UpNext
          upcoming={upcoming}
          showGenerate={showGenerate}
          generating={generateNow.isPending}
          onGenerate={() => generateNow.mutate()}
        />
      )}
      {generateNow.isError && (
        <p role="alert" className="text-sm text-red-700">
          {generateNow.error.message}
        </p>
      )}
      <div className="flex items-center justify-end gap-4">
        {data.previous && <PreviousEditionLink briefing={data.previous} />}
        <PastBriefingsLink />
      </div>
    </div>
  )
}

// The next edition as it builds. Hidden when there is nothing to say: no
// slot, nothing collected, nothing processing.
function UpNext({
  upcoming,
  showGenerate,
  generating,
  onGenerate,
}: {
  upcoming: BriefingUpcoming
  showGenerate: boolean
  generating: boolean
  onGenerate: () => void
}) {
  const parts: string[] = []
  if (upcoming.next_run_at) parts.push(formatUpcomingSlot(upcoming.next_run_at))
  if (upcoming.new_episode_count > 0) parts.push(`${plural(upcoming.new_episode_count, 'new episode')} so far`)
  if (upcoming.pending_count > 0) parts.push(`${upcoming.pending_count} still processing`)
  if (parts.length === 0) return null

  return (
    <div role="status" className="flex items-center gap-3 px-4 py-2 text-sm text-gray-600">
      <p className="flex-1 min-w-0">
        <span className="font-medium text-gray-700">Up next</span>
        {' • '}
        {parts.join(' • ')}
      </p>
      {showGenerate && (
        <button
          type="button"
          onClick={onGenerate}
          disabled={generating}
          className="text-xs text-gray-500 hover:text-primary-700 hover:underline disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {generating ? 'Generating…' : 'Generate now'}
        </button>
      )}
    </div>
  )
}

function PreviousEditionLink({ briefing }: { briefing: Briefing }) {
  return (
    <Link
      to={`/briefings/${briefing.id}`}
      className="text-xs text-gray-500 hover:text-primary-700 hover:underline"
    >
      Earlier: {editionTitle(briefing.created_at)} • {plural(briefing.episode_count, 'episode')}
      {briefing.listened_at ? ' • listened' : ''}
    </Link>
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
