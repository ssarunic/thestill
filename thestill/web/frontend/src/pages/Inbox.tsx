import { useState, useMemo } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useArrivingSoon, useInboxInfinite } from '../hooks/useApi'
import { useDebouncedSearchParam } from '../hooks/useDebouncedSearchParam'
import type { InboxItem, InboxState } from '../api/types'
import BriefingCard from '../components/BriefingCard'
import Button, { PlusIcon } from '../components/Button'
import ImportEpisodeModal from '../components/ImportEpisodeModal'
import ListGroup from '../components/ListGroup'
import ListRow, { ListRowArtwork } from '../components/ListRow'
import SearchBox from '../components/SearchBox'
import { ProgressPill } from '../components/InboxProgress'
import { deriveProgress } from '../utils/inbox'

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

// Spec #88: mail-style views over the same immutable delivery log. ``All``
// is the triage list (dismissed hidden); the others filter by row state.
type InboxView = 'all' | 'unread' | 'saved'

const VIEWS: { key: InboxView; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'saved', label: 'Saved' },
]

const VIEW_STATE: Record<InboxView, InboxState | undefined> = {
  all: undefined,
  unread: 'unread',
  saved: 'saved',
}

function parseView(raw: string | null): InboxView {
  return raw === 'unread' || raw === 'saved' ? raw : 'all'
}

function ViewSwitch({ view, onChange }: { view: InboxView; onChange: (view: InboxView) => void }) {
  return (
    <div role="group" aria-label="Inbox view" className="inline-flex rounded-lg bg-gray-100 p-0.5">
      {VIEWS.map(({ key, label }) => (
        <button
          key={key}
          type="button"
          aria-pressed={view === key}
          onClick={() => onChange(key)}
          className={`px-3 py-1 text-sm font-medium rounded-md transition-colors ${
            view === key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// Spec #88 "Arriving soon": followed podcasts' episodes still in the
// pipeline. Read-only, not inbox rows; each leaves the strip when publish
// fan-out delivers it. A failed request hides the strip rather than
// pretending nothing is on its way.
function ArrivingSoon() {
  const { data, isError } = useArrivingSoon()
  if (isError || !data || data.items.length === 0) return null
  const more = data.total - data.items.length
  return (
    <section aria-labelledby="arriving-soon-heading" data-testid="arriving-soon">
      <h2 id="arriving-soon-heading" className="text-sm font-medium text-gray-500 mb-2">
        Arriving soon <span className="text-gray-400">· {data.total}</span>
      </h2>
      <ul className="space-y-1">
        {data.items.map(({ episode, podcast }) => (
          <li key={episode.id}>
            <Link
              to={`/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`}
              className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-gray-50"
            >
              <ListRowArtwork sources={[episode.image_url, podcast.image_url]} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-gray-500">{podcast.title}</span>
                <span className="block truncate text-sm text-gray-700">{episode.title}</span>
              </span>
              <ProgressPill status={deriveProgress(episode)} />
            </Link>
          </li>
        ))}
      </ul>
      {more > 0 && <p className="mt-1 px-2 text-xs text-gray-400">and {more} more</p>}
    </section>
  )
}

export default function Inbox() {
  const [isImportModalOpen, setIsImportModalOpen] = useState(false)

  // Spec #88: the view lives in ``?view=`` beside ``?q=`` so Back restores
  // both; switching views keeps the search, clearing the search keeps the view.
  const [searchParams, setSearchParams] = useSearchParams()
  const view = parseView(searchParams.get('view'))
  const setView = (next: InboxView) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        if (next === 'all') params.delete('view')
        else params.set('view', next)
        return params
      },
      { replace: true },
    )

  // Spec #85: the search filters the user's own deliveries server-side (the
  // list is paginated, so a client-side filter would miss unloaded pages).
  // It lives in ``?q=`` so Back restores it.
  const { value: searchInput, debouncedValue: q, setValue: setSearchInput } =
    useDebouncedSearchParam('q')
  const isFiltering = q.length > 0
  const searchBoxProps = {
    value: searchInput,
    onChange: setSearchInput,
    placeholder: 'Search your inbox',
    ariaLabel: 'Search your inbox',
  }

  // Poll while at least one episode is still working through the pipeline.
  // Once everything is summarised or failed the query goes back to its
  // default 15s staleTime. A search is a lookup, not a live view: no poll.
  const POLL_INTERVAL_MS = 5_000
  const { data, isLoading, error, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useInboxInfinite({
      state: VIEW_STATE[view],
      q: isFiltering ? q : undefined,
      refetchInterval: isFiltering
        ? false
        : (query) => {
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
            {isLoading
              ? 'Loading…'
              : `${items.length}${hasNextPage ? '+' : ''} ${
                  isFiltering ? 'matching' : view === 'all' ? 'delivered' : view
                }`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden sm:block">
            <SearchBox {...searchBoxProps} inputClassName="py-1.5 w-56" testId="inbox-search-input" />
          </div>
          <Button
            onClick={() => setIsImportModalOpen(true)}
            icon={<PlusIcon />}
            iconOnlyMobile
          >
            Import
          </Button>
        </div>
      </div>

      {/* Mobile: full-width search below the header */}
      <div className="sm:hidden">
        <SearchBox {...searchBoxProps} inputClassName="py-2 w-full" />
      </div>

      <ViewSwitch view={view} onChange={setView} />

      {/* The briefing and the arriving strip belong to the whole inbox, not
          to a search result or a state-filtered view. */}
      {!isFiltering && view === 'all' && <BriefingCard />}
      {!isFiltering && view === 'all' && <ArrivingSoon />}

      {isLoading ? (
        <ListGroup>
          {[...Array(4)].map((_, i) => (
            <li key={i} className="animate-pulse h-20" />
          ))}
        </ListGroup>
      ) : items.length === 0 && isFiltering ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            Nothing in your inbox matches “{q}”
          </h3>
          <p className="text-gray-500 mb-4">
            Search matches episode titles, podcast names and descriptions of episodes delivered to you.
          </p>
          <div className="flex items-center justify-center gap-6">
            <button
              type="button"
              onClick={() => setSearchInput('')}
              data-testid="inbox-search-clear"
              className="text-primary-600 hover:text-primary-800 text-sm font-medium"
            >
              Clear search
            </button>
            <Link
              to={`/search?q=${encodeURIComponent(q)}`}
              className="text-primary-600 hover:text-primary-800 text-sm font-medium"
            >
              Search everything →
            </Link>
          </div>
        </div>
      ) : items.length === 0 && view !== 'all' ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200" data-testid="inbox-view-empty">
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            {view === 'unread' ? 'Nothing unread. Nice.' : 'Nothing saved yet'}
          </h3>
          {view === 'saved' && (
            <p className="text-gray-500">
              Save an episode from its page or the import dialog to find it here.
            </p>
          )}
        </div>
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
