import { useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  useBriefing,
  useBriefingScript,
  useMarkBriefingListened,
} from '../hooks/useApi'

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function BriefingDetail() {
  const { briefingId } = useParams<{ briefingId: string }>()
  const briefingQuery = useBriefing(briefingId ?? null)
  const scriptQuery = useBriefingScript(briefingId ?? null)
  const markListened = useMarkBriefingListened()

  if (briefingQuery.isLoading) {
    return (
      <div className="space-y-4">
        <div className="animate-pulse h-8 w-48 bg-gray-100 rounded" />
        <div className="animate-pulse h-4 w-72 bg-gray-100 rounded" />
        <div className="animate-pulse h-64 bg-white border border-gray-200 rounded-lg" />
      </div>
    )
  }

  if (briefingQuery.error || !briefingQuery.data) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Briefing not found</h2>
          <p className="text-red-600 text-sm">
            {briefingQuery.error?.message ?? 'No briefing matches that id.'}
          </p>
        </div>
      </div>
    )
  }

  const briefing = briefingQuery.data
  const isListened = !!briefing.listened_at

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Today's briefing</h1>
        <p className="text-sm text-gray-500 mt-1">
          {briefing.episode_count} episode{briefing.episode_count === 1 ? '' : 's'}
          {' • '}
          generated {formatDateTime(briefing.created_at)}
          {isListened ? ` • listened ${formatDateTime(briefing.listened_at!)}` : ''}
        </p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-6">
        {scriptQuery.isLoading && (
          <div className="space-y-2">
            <div className="animate-pulse h-4 w-3/4 bg-gray-100 rounded" />
            <div className="animate-pulse h-4 w-2/3 bg-gray-100 rounded" />
            <div className="animate-pulse h-4 w-5/6 bg-gray-100 rounded" />
          </div>
        )}
        {scriptQuery.error && (
          <p className="text-gray-500 italic">
            Briefing script not available yet.
          </p>
        )}
        {scriptQuery.data && (
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown>{scriptQuery.data.markdown}</ReactMarkdown>
          </div>
        )}
      </div>

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => markListened.mutate(briefing.id)}
          disabled={isListened || markListened.isPending}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          {isListened ? 'Marked listened' : markListened.isPending ? 'Saving…' : 'Mark listened'}
        </button>
      </div>
    </div>
  )
}
