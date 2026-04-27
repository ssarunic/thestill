import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { addPodcast, getTopPodcasts } from '../api/client'
import type { TopPodcast } from '../api/types'
import { flagFor } from '../utils/regions'
import Button, { CloseIcon } from './Button'

interface AddPodcastModalProps {
  isOpen: boolean
  onClose: () => void
}

// Branches the input box into one of three behaviours. The URL detector is
// intentionally conservative — it only fires when the input contains a
// recognisable scheme (``http://``, ``https://``, or any ``foo://``). A typed
// podcast title that happens to contain dots ("feeds.com") is treated as a
// search query, not a URL.
type AddInputState =
  | { kind: 'empty' }
  | { kind: 'url'; value: string }
  | { kind: 'query'; value: string }

const URL_RE = /^[A-Za-z][A-Za-z0-9+.\-]*:\/\//

export function parseAddInput(raw: string): AddInputState {
  const text = raw.trim()
  if (text.length === 0) return { kind: 'empty' }
  if (URL_RE.test(text)) return { kind: 'url', value: text }
  if (text.length >= 2) return { kind: 'query', value: text }
  return { kind: 'empty' }
}

const QUERY_DEBOUNCE_MS = 250
const SEARCH_LIMIT = 10

export default function AddPodcastModal({ isOpen, onClose }: AddPodcastModalProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [input, setInput] = useState('')
  const [results, setResults] = useState<TopPodcast[]>([])
  const [region, setRegion] = useState<string | null>(null)
  const [isFetching, setIsFetching] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  // ``rss_url``-keyed sets cover both list-Follow and URL-paste branches with
  // the same shape. ``addDone`` persists for the modal's open lifetime so
  // already-followed rows in the same session keep their checkmark.
  const [addInFlight, setAddInFlight] = useState<Set<string>>(new Set())
  const [addDone, setAddDone] = useState<Set<string>>(new Set())
  const [addError, setAddError] = useState<string | null>(null)
  const [cursorIdx, setCursorIdx] = useState<number>(-1)

  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const parsed = useMemo(() => parseAddInput(input), [input])

  // Reset all per-open UI state when the modal opens. ``addDone`` is reset
  // alongside everything else — the next fetch returns authoritative
  // ``is_following`` values, so a stale cached set from the previous open
  // could only contradict the server (e.g. if the user unfollowed elsewhere).
  // It exists purely to flip a button optimistically *during* a single
  // open session.
  useEffect(() => {
    if (!isOpen) return
    setInput('')
    setCursorIdx(-1)
    setAddError(null)
    setAddInFlight(new Set())
    setAddDone(new Set())
    inputRef.current?.focus()
  }, [isOpen])

  // Reset highlight whenever results change so ↑/↓ doesn't point at a stale row.
  useEffect(() => {
    setCursorIdx(-1)
  }, [results])

  // Fetch effect. Three paths:
  //   - empty: load top-N for the user's region (no debounce).
  //   - query: debounced search via ?q=.
  //   - url:   no fetch; the list area gives way to an "Add this feed" row.
  useEffect(() => {
    if (!isOpen) return

    if (parsed.kind === 'url') {
      // Hide the list immediately; clearing here means stale results from a
      // prior query state don't flash through during the typing transition.
      setResults([])
      setFetchError(null)
      setIsFetching(false)
      return
    }

    const runFetch = async (q: string | undefined) => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      setIsFetching(true)
      setFetchError(null)
      try {
        const response = await getTopPodcasts(undefined, SEARCH_LIMIT, q, ctrl.signal)
        if (ctrl.signal.aborted) return
        setResults(response.top_podcasts)
        setRegion(response.region)
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        setFetchError("Couldn't load suggestions — paste an RSS URL above.")
        setResults([])
      } finally {
        if (!ctrl.signal.aborted) setIsFetching(false)
      }
    }

    if (parsed.kind === 'empty') {
      // Initial / cleared state — no debounce; users opening the modal
      // shouldn't wait 250ms to see the list.
      runFetch(undefined)
      return
    }

    // ``query``: debounce, then fetch.
    const timer = setTimeout(() => {
      runFetch(parsed.value)
    }, QUERY_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [isOpen, parsed])

  // Abort any in-flight request when the modal closes.
  useEffect(() => {
    if (!isOpen) abortRef.current?.abort()
  }, [isOpen])

  const handleFollow = useCallback(
    async (rssUrl: string) => {
      if (!rssUrl) return
      if (addInFlight.has(rssUrl) || addDone.has(rssUrl)) return
      setAddInFlight((prev) => {
        const next = new Set(prev)
        next.add(rssUrl)
        return next
      })
      setAddError(null)
      try {
        await addPodcast({ url: rssUrl })
        setAddDone((prev) => {
          const next = new Set(prev)
          next.add(rssUrl)
          return next
        })
        // Refresh the user's followed-list view so the Podcasts page picks
        // up the new row once the background pipeline finishes adding it.
        queryClient.invalidateQueries({ queryKey: ['podcasts'] })
        queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      } catch (err) {
        setAddError(err instanceof Error ? err.message : 'Failed to add podcast')
      } finally {
        setAddInFlight((prev) => {
          const next = new Set(prev)
          next.delete(rssUrl)
          return next
        })
      }
    },
    [addInFlight, addDone, queryClient],
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (parsed.kind === 'url') {
        if (e.key === 'Enter') {
          e.preventDefault()
          void handleFollow(parsed.value)
        }
        return
      }
      if (results.length === 0) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setCursorIdx((idx) => Math.min(idx + 1, results.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setCursorIdx((idx) => Math.max(idx - 1, -1))
      } else if (e.key === 'Enter' && cursorIdx >= 0) {
        e.preventDefault()
        void handleFollow(results[cursorIdx].rss_url)
      }
    },
    [parsed, results, cursorIdx, handleFollow, onClose],
  )

  const handleChangeRegion = useCallback(() => {
    onClose()
    navigate('/settings')
  }, [navigate, onClose])

  if (!isOpen) return null

  const showList = parsed.kind !== 'url'
  const showUrlRow = parsed.kind === 'url'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
      onKeyDown={(e) => {
        // Bare Escape on the backdrop (when input isn't focused) still closes.
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">Follow a podcast</h2>
          <Button
            variant="ghost"
            size="sm"
            icon={<CloseIcon />}
            onClick={onClose}
            aria-label="Close"
          />
        </div>

        {/* Input */}
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Search top 500 or paste an RSS URL…"
          aria-label="Search top podcasts or paste a URL"
          aria-activedescendant={cursorIdx >= 0 ? `top-podcast-row-${cursorIdx}` : undefined}
          className="w-full px-4 py-3 border rounded-lg text-gray-900 placeholder-gray-400 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-colors bg-white"
        />

        {/* Region badge — only meaningful in list mode */}
        {showList && region && (
          <p className="mt-3 mb-2 text-sm text-gray-500 flex items-center justify-between">
            <span>
              Top in {flagFor(region)} {region.toUpperCase()}
            </span>
            <button
              onClick={handleChangeRegion}
              className="text-primary-700 hover:underline text-xs"
            >
              Change region
            </button>
          </p>
        )}

        {/* URL paste row */}
        {showUrlRow && (
          <div className="mt-4 flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 truncate">Add this feed</div>
              <div className="text-xs text-gray-500 truncate">{parsed.value}</div>
            </div>
            <FollowButton
              status={statusOf(parsed.value, addInFlight, addDone)}
              onClick={() => handleFollow(parsed.value)}
              labels={{ idle: 'Add', pending: 'Adding…', done: 'Added ✓' }}
            />
          </div>
        )}

        {/* List */}
        {showList && (
          <div className="mt-2">
            {fetchError && (
              <p className="py-3 text-sm text-red-600">{fetchError}</p>
            )}
            {!fetchError && isFetching && results.length === 0 && (
              <ListSkeleton />
            )}
            {!fetchError && !isFetching && results.length === 0 && (
              <p className="py-3 text-sm text-gray-500">No matches.</p>
            )}
            {!fetchError && results.length > 0 && (
              <ol className="divide-y divide-gray-100 border border-gray-100 rounded-lg max-h-80 overflow-y-auto">
                {results.map((podcast, idx) => {
                  const isFollowed =
                    podcast.is_following || addDone.has(podcast.rss_url)
                  const status = isFollowed
                    ? 'done'
                    : addInFlight.has(podcast.rss_url)
                      ? 'pending'
                      : 'idle'
                  const isHighlighted = idx === cursorIdx
                  return (
                    <li
                      key={podcast.rss_url}
                      id={`top-podcast-row-${idx}`}
                      className={`flex items-center gap-3 px-3 py-2 ${
                        isHighlighted ? 'bg-indigo-50' : ''
                      }`}
                    >
                      <span className="w-6 text-right text-sm tabular-nums text-gray-400 flex-shrink-0">
                        {podcast.rank}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900 truncate">
                          {podcast.name}
                        </div>
                        <div className="text-xs text-gray-500 truncate">
                          {podcast.artist ?? 'Unknown artist'}
                          {podcast.category && (
                            <span className="ml-2 text-gray-400">· {podcast.category}</span>
                          )}
                        </div>
                      </div>
                      <FollowButton
                        status={status}
                        onClick={() => handleFollow(podcast.rss_url)}
                        labels={{ idle: 'Follow', pending: 'Adding…', done: 'Following ✓' }}
                      />
                    </li>
                  )
                })}
              </ol>
            )}
            <p className="mt-3 text-xs text-gray-400">
              Don&apos;t see it? Paste the RSS URL above.
            </p>
          </div>
        )}

        {addError && (
          <p className="mt-3 text-sm text-red-600">{addError}</p>
        )}
      </div>
    </div>
  )
}

type FollowStatus = 'idle' | 'pending' | 'done'

function statusOf(key: string, inFlight: Set<string>, done: Set<string>): FollowStatus {
  if (done.has(key)) return 'done'
  if (inFlight.has(key)) return 'pending'
  return 'idle'
}

interface FollowButtonProps {
  status: FollowStatus
  onClick: () => void
  labels: Record<FollowStatus, string>
}

function FollowButton({ status, onClick, labels }: FollowButtonProps) {
  const disabled = status !== 'idle'
  const isDone = status === 'done'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-3 py-1.5 rounded-md text-xs font-medium flex-shrink-0 ${
        isDone
          ? 'bg-green-100 text-green-800 cursor-default'
          : 'bg-primary-900 text-white hover:bg-primary-800 disabled:opacity-50'
      }`}
    >
      {labels[status]}
    </button>
  )
}

function ListSkeleton() {
  return (
    <ol className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
      {[0, 1, 2].map((i) => (
        <li key={i} className="flex items-center gap-3 px-3 py-3 animate-pulse">
          <span className="w-6 h-3 bg-gray-200 rounded" />
          <div className="flex-1 space-y-2">
            <div className="h-3 bg-gray-200 rounded w-3/4" />
            <div className="h-2 bg-gray-200 rounded w-1/2" />
          </div>
          <span className="w-16 h-6 bg-gray-200 rounded" />
        </li>
      ))}
    </ol>
  )
}
