import { useState, useEffect, useCallback } from 'react'
import { usePodcasts } from '../hooks/useApi'
import type { EpisodeFilters, EpisodeState } from '../api/types'

interface EpisodeFiltersProps {
  filters: EpisodeFilters
  onFiltersChange: (filters: EpisodeFilters) => void
}

const stateOptions: { value: EpisodeState | ''; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'discovered', label: 'Discovered' },
  { value: 'downloaded', label: 'Downloaded' },
  { value: 'downsampled', label: 'Downsampled' },
  { value: 'transcribed', label: 'Transcribed' },
  { value: 'cleaned', label: 'Cleaned' },
  { value: 'summarized', label: 'Ready' },
]

const sortOptions: { value: string; label: string }[] = [
  { value: 'pub_date-desc', label: 'Newest first' },
  { value: 'pub_date-asc', label: 'Oldest first' },
  { value: 'title-asc', label: 'Title A-Z' },
  { value: 'title-desc', label: 'Title Z-A' },
  { value: 'updated_at-desc', label: 'Recently updated' },
]

export default function EpisodeFilters({ filters, onFiltersChange }: EpisodeFiltersProps) {
  const [searchInput, setSearchInput] = useState(filters.search || '')
  const { data: podcastsData } = usePodcasts()

  // Debounce search input
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (searchInput !== (filters.search || '')) {
        onFiltersChange({ ...filters, search: searchInput || undefined })
      }
    }, 300)
    return () => clearTimeout(timeoutId)
  }, [searchInput, filters, onFiltersChange])

  const handlePodcastChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value
      onFiltersChange({ ...filters, podcast_slug: value || undefined })
    },
    [filters, onFiltersChange]
  )

  const handleStateChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value as EpisodeState | ''
      onFiltersChange({ ...filters, state: value || undefined })
    },
    [filters, onFiltersChange]
  )

  const handleSortChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const [sort_by, sort_order] = e.target.value.split('-') as [
        'pub_date' | 'title' | 'updated_at',
        'asc' | 'desc'
      ]
      onFiltersChange({ ...filters, sort_by, sort_order })
    },
    [filters, onFiltersChange]
  )

  const handleDateFromChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value
      onFiltersChange({ ...filters, date_from: value || undefined })
    },
    [filters, onFiltersChange]
  )

  const handleDateToChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value
      onFiltersChange({ ...filters, date_to: value || undefined })
    },
    [filters, onFiltersChange]
  )

  const handleClearFilters = useCallback(() => {
    setSearchInput('')
    onFiltersChange({})
  }, [onFiltersChange])

  const hasActiveFilters =
    filters.search ||
    filters.podcast_slug ||
    filters.state ||
    filters.date_from ||
    filters.date_to ||
    (filters.sort_by && filters.sort_by !== 'pub_date') ||
    (filters.sort_order && filters.sort_order !== 'desc')

  const currentSortValue = `${filters.sort_by || 'pub_date'}-${filters.sort_order || 'desc'}`

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex flex-col gap-4">
        {/* Search input */}
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <svg className="h-5 w-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
          </div>
          <input
            type="text"
            placeholder="Search episodes by title..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-lg text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          />
        </div>

        {/* Filter row */}
        <div className="flex flex-wrap gap-3">
          {/* Podcast filter */}
          <select
            value={filters.podcast_slug || ''}
            onChange={handlePodcastChange}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          >
            <option value="">All podcasts</option>
            {podcastsData?.podcasts.map((podcast) => (
              <option key={podcast.slug} value={podcast.slug}>
                {podcast.title}
              </option>
            ))}
          </select>

          {/* Status filter */}
          <select
            value={filters.state || ''}
            onChange={handleStateChange}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          >
            {stateOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          {/* Date from */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-gray-500">From:</label>
            <input
              type="date"
              value={filters.date_from || ''}
              onChange={handleDateFromChange}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>

          {/* Date to */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-gray-500">To:</label>
            <input
              type="date"
              value={filters.date_to || ''}
              onChange={handleDateToChange}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>

          {/* Sort */}
          <select
            value={currentSortValue}
            onChange={handleSortChange}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          >
            {sortOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          {/* Clear filters */}
          {hasActiveFilters && (
            <button
              onClick={handleClearFilters}
              className="px-3 py-2 text-sm text-gray-600 hover:text-gray-900 flex items-center gap-1"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
              Clear filters
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
