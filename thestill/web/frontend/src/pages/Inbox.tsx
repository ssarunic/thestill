import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useInbox } from '../hooks/useApi'
import type { Episode, InboxItem, InboxState } from '../api/types'
import Button, { PlusIcon } from '../components/Button'
import ImportEpisodeModal from '../components/ImportEpisodeModal'

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

// Pipeline progress as the user perceives it. Derived from episode state +
// failure flags rather than stored separately so two users on the same
// episode see consistent progress (spec #31, "inbox row state computed from
// (episode.state, entity_extraction_status)").
type ProgressKind = 'failed' | 'processing' | 'ready'

interface ProgressStatus {
  kind: ProgressKind
  label: string
}

function deriveProgress(episode: Episode): ProgressStatus {
  if (episode.is_failed) {
    return { kind: 'failed', label: 'Failed' }
  }
  switch (episode.state) {
    case 'discovered':
      return { kind: 'processing', label: 'Downloading…' }
    case 'downloaded':
    case 'downsampled':
      return { kind: 'processing', label: 'Transcribing…' }
    case 'transcribed':
      return { kind: 'processing', label: 'Cleaning…' }
    case 'cleaned':
      return { kind: 'processing', label: 'Summarising…' }
    case 'summarized':
      return { kind: 'ready', label: 'Ready' }
    default:
      return { kind: 'processing', label: 'Processing…' }
  }
}

function ProgressPill({ status }: { status: ProgressStatus }) {
  const cls =
    status.kind === 'failed'
      ? 'bg-red-100 text-red-700'
      : status.kind === 'ready'
        ? 'bg-green-100 text-green-700'
        : 'bg-amber-100 text-amber-800'
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded ${cls}`}>
      {status.kind === 'processing' && (
        <span
          aria-hidden="true"
          className="inline-block w-2 h-2 rounded-full bg-current animate-pulse"
        />
      )}
      {status.label}
    </span>
  )
}

function InboxRow({ item }: { item: InboxItem }) {
  const { entry, episode, podcast } = item
  const episodeHref = `/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`
  const progress = deriveProgress(episode)
  // Only surface the progress pill while the row hasn't reached the inbox's
  // "ready to read" state — once summarised, the regular state badge is
  // enough signal.
  const showProgress = progress.kind !== 'ready'
  return (
    <li className="flex items-start gap-4 p-4 bg-white border border-gray-200 rounded-lg">
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
          {entry.source === 'import' && (
            <span className="text-xs text-gray-400 italic">imported</span>
          )}
        </div>
        <Link
          to={episodeHref}
          className="block text-base font-medium text-gray-900 hover:text-primary-600 truncate"
        >
          {episode.title}
        </Link>
      </div>
      <div className="flex flex-col items-end gap-1 flex-shrink-0">
        <StateBadge state={entry.state} />
        {showProgress && <ProgressPill status={progress} />}
      </div>
    </li>
  )
}

export default function Inbox() {
  const [isImportModalOpen, setIsImportModalOpen] = useState(false)

  // Poll while at least one episode is still working through the pipeline.
  // Once everything is summarised or failed the query goes back to its
  // default 15s staleTime.
  const POLL_INTERVAL_MS = 5_000
  const { data, isLoading, error } = useInbox({
    refetchInterval: (query) => {
      const items = query.state.data?.items
      if (!items) return false
      return items.some((it) => deriveProgress(it.episode).kind === 'processing')
        ? POLL_INTERVAL_MS
        : false
    },
  })

  const items = useMemo(() => data?.items ?? [], [data])

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

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Inbox</h1>
          <p className="text-gray-500 mt-1">
            {isLoading ? 'Loading…' : `${items.length} delivered`}
          </p>
        </div>
        <Button
          onClick={() => setIsImportModalOpen(true)}
          icon={<PlusIcon />}
          iconOnlyMobile
        >
          Import
        </Button>
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
          <p className="text-gray-500 mb-4">
            Follow a podcast to receive new episodes — or paste a link to import one.
          </p>
          <Button onClick={() => setIsImportModalOpen(true)} icon={<PlusIcon />}>
            Import episode
          </Button>
        </div>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <InboxRow key={item.entry.id} item={item} />
          ))}
        </ul>
      )}

      <ImportEpisodeModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
      />
    </div>
  )
}
