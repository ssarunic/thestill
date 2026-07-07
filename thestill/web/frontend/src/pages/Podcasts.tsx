import { useState, useEffect, useRef, useCallback } from 'react'
import { usePodcastsInfinite } from '../hooks/useApi'
import PodcastCard from '../components/PodcastCard'
import AddPodcastModal from '../components/AddPodcastModal'
import Button, { PlusIcon } from '../components/Button'

interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  inputClassName: string
  testId?: string
}

function SearchBox({ value, onChange, inputClassName, testId }: SearchBoxProps) {
  return (
    <div className="relative">
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter podcasts…"
        aria-label="Filter podcasts"
        className={`rounded-md border border-gray-300 pl-8 pr-7 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 ${inputClassName}`}
        data-testid={testId}
      />
      <svg
        className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 110-16 8 8 0 010 16z" />
      </svg>
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear search"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm leading-none px-1"
        >
          ×
        </button>
      )}
    </div>
  )
}

export default function Podcasts() {
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  // Search state — ``searchInput`` is what the user types; ``debouncedQ`` is
  // what we actually send to the API. The filter is server-side (the list is
  // paginated, so a client-side filter would miss unloaded pages).
  const [searchInput, setSearchInput] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(searchInput.trim()), 250)
    return () => clearTimeout(t)
  }, [searchInput])

  const {
    data,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = usePodcastsInfinite(12, debouncedQ || undefined)

  // Intersection Observer for infinite scroll
  const loadMoreRef = useRef<HTMLDivElement>(null)

  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const [entry] = entries
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage]
  )

  useEffect(() => {
    const element = loadMoreRef.current
    if (!element) return

    const observer = new IntersectionObserver(handleObserver, {
      root: null,
      rootMargin: '100px',
      threshold: 0,
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [handleObserver])

  // Flatten all pages into a single podcasts array
  const allPodcasts = data?.pages.flatMap((page) => page.podcasts) ?? []
  const totalPodcasts = data?.pages[0]?.total ?? 0

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading podcasts</h2>
          <p className="text-red-600 text-sm">{error.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Podcasts</h1>
          <p className="text-gray-500 mt-1">
            {isLoading
              ? 'Loading...'
              : debouncedQ
                ? `${totalPodcasts} podcast${totalPodcasts === 1 ? '' : 's'} matching "${debouncedQ}"`
                : `${totalPodcasts} podcasts tracked`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden sm:block">
            <SearchBox
              value={searchInput}
              onChange={setSearchInput}
              inputClassName="py-1.5 w-56"
              testId="podcasts-search-input"
            />
          </div>
          <Button
            onClick={() => setIsAddModalOpen(true)}
            icon={<PlusIcon />}
            iconOnlyMobile
          >
            Follow
          </Button>
        </div>
      </div>

      {/* Mobile: full-width search below the header */}
      <div className="sm:hidden">
        <SearchBox value={searchInput} onChange={setSearchInput} inputClassName="py-2 w-full" />
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="animate-pulse bg-white rounded-lg border border-gray-200 p-6">
              <div className="flex gap-4">
                <div className="w-16 h-16 bg-gray-200 rounded-lg" />
                <div className="flex-1 space-y-3">
                  <div className="h-4 bg-gray-200 rounded w-3/4" />
                  <div className="h-3 bg-gray-200 rounded w-1/2" />
                </div>
              </div>
              <div className="mt-4 h-2 bg-gray-200 rounded" />
            </div>
          ))}
        </div>
      ) : allPodcasts.length === 0 ? (
        debouncedQ ? (
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <h3 className="text-lg font-medium text-gray-900 mb-2">No matches</h3>
            <p className="text-gray-500 mb-4">
              None of your podcasts match "{debouncedQ}". Try the global search for episode content.
            </p>
            <button
              type="button"
              onClick={() => setSearchInput('')}
              className="text-primary-600 hover:text-primary-800 text-sm font-medium"
            >
              Clear filter
            </button>
          </div>
        ) : (
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <svg className="w-16 h-16 mx-auto text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
            <h3 className="text-lg font-medium text-gray-900 mb-2">No podcasts followed</h3>
            <p className="text-gray-500 mb-4">Click the Follow button to get started.</p>
            <Button
              onClick={() => setIsAddModalOpen(true)}
              icon={<PlusIcon />}
            >
              Follow Podcast
            </Button>
          </div>
        )
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {allPodcasts.map((podcast) => (
              <PodcastCard key={podcast.index} podcast={podcast} />
            ))}
          </div>

          {/* Load more trigger */}
          <div ref={loadMoreRef} className="py-4">
            {isFetchingNextPage && (
              <div className="flex justify-center">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
              </div>
            )}
            {!hasNextPage && allPodcasts.length > 0 && (
              <p className="text-center text-gray-400 text-sm">All podcasts loaded</p>
            )}
          </div>
        </>
      )}

      {/* Add Podcast Modal */}
      <AddPodcastModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
      />
    </div>
  )
}
