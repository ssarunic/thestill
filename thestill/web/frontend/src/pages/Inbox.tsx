import { useState, useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { useInboxInfinite } from '../hooks/useApi'
import type { Episode, InboxItem } from '../api/types'
import BriefingCard from '../components/BriefingCard'
import Button, { PlusIcon } from '../components/Button'
import ImportEpisodeModal from '../components/ImportEpisodeModal'
import ListGroup from '../components/ListGroup'
import ListRow, { ListRowArtwork } from '../components/ListRow'

// Compact, single-token timestamp: today → "12:50", this year → "8 Aug",
// older → "8 Aug 24". Never wraps, so the meta row stays one line on phones.
function formatDelivered(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  }
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: '2-digit' }),
  })
}

// Read state is conveyed like an email client: title weight is the *sole*
// unread signal (a dot alongside it was redundant), quiet styling for read,
// a bookmark glyph for saved, and a dimmed row for dismissed. Screen readers
// still get the state as text.
function SavedIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      className="w-3.5 h-3.5 text-yellow-500 flex-shrink-0"
    >
      <path d="M5 3a2 2 0 0 0-2 2v12l7-4 7 4V5a2 2 0 0 0-2-2H5z" />
    </svg>
  )
}

// Pipeline progress as the user perceives it. Derived from episode state +
// failure flags so two users sharing an imported episode see consistent
// progress without storing per-user pipeline state.
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
  // Spec #52 — carrying the inbox location in navigation state makes App
  // render the episode in the reader overlay above the still-mounted list.
  // Cmd/middle-click opens a new tab with no state → standalone page.
  const location = useLocation()
  const episodeHref = `/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`
  const progress = deriveProgress(episode)
  // Only surface the progress pill while the row hasn't reached the inbox's
  // "ready to read" state — once summarised, the read-state styling is
  // enough signal.
  const showProgress = progress.kind !== 'ready'
  const isUnread = entry.state === 'unread'
  const isDismissed = entry.state === 'dismissed'
  return (
    <ListRow
      align="start"
      to={episodeHref}
      state={{ backgroundLocation: location }}
      className={isDismissed ? 'opacity-60' : ''}
      // Episode artwork first (matches the reader header), podcast artwork as
      // the fallback when the feed item carries none.
      leading={<ListRowArtwork sources={[episode.image_url, podcast.image_url]} />}
      overline={
        <div className="flex items-center gap-1.5">
          {entry.state === 'saved' && <SavedIcon />}
          <p className="flex-1 min-w-0 truncate text-xs sm:text-sm text-gray-500">
            {podcast.title}
          </p>
          {entry.source === 'import' && (
            <span className="text-xs text-gray-400 italic flex-shrink-0">imported</span>
          )}
          <time
            dateTime={entry.delivered_at}
            className="text-xs text-gray-400 whitespace-nowrap flex-shrink-0"
          >
            {formatDelivered(entry.delivered_at)}
          </time>
          <span className="sr-only">{entry.state}</span>
        </div>
      }
      title={episode.title}
      titleClassName={`group-hover:text-primary-600 ${
        isUnread ? 'font-semibold text-gray-900' : 'font-normal text-gray-600'
      }`}
      footer={
        showProgress ? (
          <div className="mt-1.5">
            <ProgressPill status={progress} />
          </div>
        ) : undefined
      }
    />
  )
}

export default function Inbox() {
  const [isImportModalOpen, setIsImportModalOpen] = useState(false)

  // Poll while at least one episode is still working through the pipeline.
  // Once everything is summarised or failed the query goes back to its
  // default 15s staleTime.
  const POLL_INTERVAL_MS = 5_000
  const { data, isLoading, error, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useInboxInfinite({
      refetchInterval: (query) => {
        const pages = query.state.data?.pages
        if (!pages) return false
        return pages.some((page) =>
          page.items.some((it) => deriveProgress(it.episode).kind === 'processing'),
        )
          ? POLL_INTERVAL_MS
          : false
      },
    })

  const items = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])

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
            {isLoading ? 'Loading…' : `${items.length}${hasNextPage ? '+' : ''} delivered`}
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

      <BriefingCard />

      {isLoading ? (
        <ListGroup>
          {[...Array(4)].map((_, i) => (
            <li key={i} className="animate-pulse h-20" />
          ))}
        </ListGroup>
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
        <>
          <ListGroup>
            {items.map((item) => (
              <InboxRow key={item.entry.id} item={item} />
            ))}
          </ListGroup>
          {hasNextPage && (
            <div className="text-center">
              <Button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
                {isFetchingNextPage ? 'Loading…' : 'Load older'}
              </Button>
            </div>
          )}
        </>
      )}

      <ImportEpisodeModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
      />
    </div>
  )
}
