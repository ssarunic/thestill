import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAllEpisodesInfinite } from '../hooks/useApi'
import EpisodeBrowserCard from '../components/EpisodeBrowserCard'
import EpisodeFilters from '../components/EpisodeFilters'
import BulkActionsBar from '../components/BulkActionsBar'
import type { EpisodeFilters as EpisodeFiltersType, EpisodeState } from '../api/types'

export default function Episodes() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // Parse filters from URL
  const filters: EpisodeFiltersType = {
    search: searchParams.get('search') || undefined,
    podcast_slug: searchParams.get('podcast') || undefined,
    state: (searchParams.get('state') as EpisodeState) || undefined,
    date_from: searchParams.get('from') || undefined,
    date_to: searchParams.get('to') || undefined,
    sort_by: (searchParams.get('sort') as 'pub_date' | 'title' | 'updated_at') || undefined,
    sort_order: (searchParams.get('order') as 'asc' | 'desc') || undefined,
  }

  const {
    data,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useAllEpisodesInfinite(filters)

  // Update URL when filters change
  const handleFiltersChange = useCallback(
    (newFilters: EpisodeFiltersType) => {
      const params = new URLSearchParams()
      if (newFilters.search) params.set('search', newFilters.search)
      if (newFilters.podcast_slug) params.set('podcast', newFilters.podcast_slug)
      if (newFilters.state) params.set('state', newFilters.state)
      if (newFilters.date_from) params.set('from', newFilters.date_from)
      if (newFilters.date_to) params.set('to', newFilters.date_to)
      if (newFilters.sort_by && newFilters.sort_by !== 'pub_date') params.set('sort', newFilters.sort_by)
      if (newFilters.sort_order && newFilters.sort_order !== 'desc') params.set('order', newFilters.sort_order)
      setSearchParams(params)
    },
    [setSearchParams]
  )

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

  // Flatten all pages into a single episodes array
  const allEpisodes = data?.pages.flatMap((page) => page.episodes) ?? []
  const totalEpisodes = data?.pages[0]?.total ?? 0

  // Selection handlers
  const handleSelect = useCallback((episodeId: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (selected) {
        next.add(episodeId)
      } else {
        next.delete(episodeId)
      }
      return next
    })
  }, [])

  const handleSelectAll = useCallback(() => {
    if (selectedIds.size === allEpisodes.length) {
      // Deselect all
      setSelectedIds(new Set())
    } else {
      // Select all visible
      setSelectedIds(new Set(allEpisodes.map((e) => e.id)))
    }
  }, [allEpisodes, selectedIds.size])

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set())
  }, [])

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading episodes</h2>
          <p className="text-red-600 text-sm">{error.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Episodes</h1>
          <p className="text-gray-500 mt-1">
            {isLoading ? 'Loading...' : `${totalEpisodes} episodes`}
          </p>
        </div>
      </div>

      {/* Filters */}
      <EpisodeFilters filters={filters} onFiltersChange={handleFiltersChange} />

      {/* Select all checkbox */}
      {allEpisodes.length > 0 && (
        <div className="flex items-center gap-3 px-1">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={selectedIds.size === allEpisodes.length && allEpisodes.length > 0}
              onChange={handleSelectAll}
              className="w-4 h-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500"
            />
            <span className="text-sm text-gray-600">
              {selectedIds.size === allEpisodes.length ? 'Deselect all' : 'Select all visible'}
            </span>
          </label>
          {selectedIds.size > 0 && (
            <span className="text-sm text-gray-500">
              ({selectedIds.size} selected)
            </span>
          )}
        </div>
      )}

      {/* Episodes list */}
      {isLoading ? (
        <div className="space-y-4">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="animate-pulse bg-white rounded-lg border border-gray-200 p-4">
              <div className="flex items-start gap-3">
                <div className="w-4 h-4 bg-gray-200 rounded" />
                <div className="w-10 h-10 bg-gray-200 rounded-md" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 bg-gray-200 rounded w-3/4" />
                  <div className="h-3 bg-gray-200 rounded w-1/2" />
                  <div className="h-3 bg-gray-200 rounded w-1/4" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : allEpisodes.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <svg
            className="w-16 h-16 mx-auto text-gray-300 mb-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
            />
          </svg>
          <h3 className="text-lg font-medium text-gray-900 mb-2">No episodes found</h3>
          <p className="text-gray-500">
            {Object.keys(filters).some((k) => filters[k as keyof EpisodeFiltersType])
              ? 'Try adjusting your filters'
              : 'Add a podcast to get started'}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {allEpisodes.map((episode) => (
              <EpisodeBrowserCard
                key={episode.id}
                episode={episode}
                isSelected={selectedIds.has(episode.id)}
                onSelect={handleSelect}
              />
            ))}
          </div>

          {/* Load more trigger */}
          <div ref={loadMoreRef} className="py-4">
            {isFetchingNextPage && (
              <div className="flex justify-center">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600"></div>
              </div>
            )}
            {!hasNextPage && allEpisodes.length > 0 && (
              <p className="text-center text-gray-400 text-sm">All episodes loaded</p>
            )}
          </div>
        </>
      )}

      {/* Bulk actions bar */}
      <BulkActionsBar selectedIds={selectedIds} onClearSelection={handleClearSelection} />
    </div>
  )
}
