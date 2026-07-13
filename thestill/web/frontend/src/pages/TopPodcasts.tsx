import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { addPodcast, resolvePodcast } from '../api/client'
import { useTopPodcasts } from '../hooks/useApi'
import { useScrollRestoration } from '../hooks/useScrollRestoration'
import type { TopPodcast } from '../api/types'
import { flagFor } from '../utils/regions'
import { useToast } from '../components/Toast'

export default function TopPodcasts() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { showToast } = useToast()
  // Restore scroll position on Back from a podcast detail page.
  useScrollRestoration()
  // Filters live in the URL query string so the browser Back button restores
  // both them *and* the scroll position when returning from a podcast detail
  // page — the app-wide convention (Episodes, Search, …). Combined with the
  // cached ``useTopPodcasts`` query, the list re-renders instantly on Back.
  const [searchParams, setSearchParams] = useSearchParams()
  const regionParam = searchParams.get('region') || undefined
  const categoryParam = searchParams.get('category') || undefined
  // Server's ``q`` validator rejects empty strings (min_length=1), so a blank
  // param reads as ``undefined``.
  const qParam = searchParams.get('q') || undefined

  const [adding, setAdding] = useState<Record<string, 'idle' | 'pending' | 'done' | 'error'>>({})
  // Per-row resolve state for the lazy-import → navigate flow. Keyed by
  // rss_url, same as ``adding`` above.
  const [resolving, setResolving] = useState<Record<string, boolean>>({})
  // ``searchInput`` is what the user types; it seeds from the URL on mount (so
  // Back navigation restores it) and is debounced back into the ``q`` param.
  const [searchInput, setSearchInput] = useState(qParam ?? '')
  const debouncedQ = qParam ?? ''

  // Merge a single filter param into the URL without clobbering the others.
  // ``replace`` keeps the list as one history entry so Back returns to the
  // detail's referrer rather than stepping through each filter change.
  const setFilterParam = (key: string, value: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  // Debounce typing into the URL ``q`` param — the 250ms gap keeps us from
  // hammering /api/top-podcasts on every keystroke. Trailing whitespace in the
  // input is preserved for the user but trimmed before it hits the URL/API.
  useEffect(() => {
    const trimmed = searchInput.trim()
    if (trimmed === (qParam ?? '')) return
    const t = setTimeout(() => setFilterParam('q', trimmed || null), 250)
    return () => clearTimeout(t)
    // setFilterParam is stable enough (setSearchParams is stable); excluding it
    // avoids re-scheduling the debounce on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput, qParam])

  const { data, isLoading, isError, error } = useTopPodcasts(
    regionParam,
    qParam,
    categoryParam,
  )

  // Trust the server's resolved region — it may differ from what we asked for
  // (e.g. no param → IP/user default).
  const region = data?.region ?? regionParam ?? null
  const available = data?.available_regions ?? []
  const availableCategories = data?.available_categories ?? []
  const category = categoryParam ?? null
  const items = data?.top_podcasts ?? []
  const loading = isLoading
  const errorMessage = isError
    ? error instanceof Error
      ? error.message
      : 'Failed to load top podcasts'
    : null

  const headerLabel = useMemo(() => {
    if (!region) return 'Top podcasts'
    return `Top podcasts ${flagFor(region)} ${region.toUpperCase()}`
  }, [region])

  const showRegionHint =
    !!user && (user.region ?? null) !== region && !!region && !!user?.region

  async function handleAdd(podcast: TopPodcast) {
    const key = podcast.rss_url
    setAdding((prev) => ({ ...prev, [key]: 'pending' }))
    try {
      await addPodcast({ url: podcast.rss_url })
      setAdding((prev) => ({ ...prev, [key]: 'done' }))
    } catch {
      setAdding((prev) => ({ ...prev, [key]: 'error' }))
    }
  }

  async function handleOpen(podcast: TopPodcast) {
    // Fast path: chart entry already linked to a local ``podcasts`` row.
    if (podcast.podcast_slug) {
      navigate(`/podcasts/${podcast.podcast_slug}`)
      return
    }

    const key = podcast.rss_url
    if (resolving[key]) return
    setResolving((prev) => ({ ...prev, [key]: true }))
    try {
      const res = await resolvePodcast({ url: podcast.rss_url })
      navigate(`/podcasts/${res.podcast_slug}`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open podcast'
      showToast(message, 'error')
      setResolving((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-baseline justify-between mb-2 gap-4 flex-wrap">
        <h1 className="text-2xl font-bold text-gray-900">{headerLabel}</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search top podcasts…"
              aria-label="Search top podcasts"
              className="rounded-md border border-gray-300 pl-8 pr-7 py-1 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 w-56"
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
            {searchInput && (
              <button
                type="button"
                onClick={() => setSearchInput('')}
                aria-label="Clear search"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm leading-none px-1"
              >
                ×
              </button>
            )}
          </div>
          <label className="text-sm text-gray-600 flex items-center gap-2">
            <span>Category</span>
            <select
              value={category ?? ''}
              onChange={(e) => setFilterParam('category', e.target.value || null)}
              aria-label="Filter by category"
              className="rounded-md border border-gray-300 px-2 py-1 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="">All</option>
              {availableCategories.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-gray-600 flex items-center gap-2">
            <span>Region</span>
            <select
              value={region ?? ''}
              onChange={(e) => setFilterParam('region', e.target.value || null)}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {available.map((code) => (
                <option key={code} value={code}>
                  {flagFor(code)} {code.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <p className="text-gray-600 mb-6">
        {user?.region_locked
          ? 'Showing your region. '
          : user?.region
            ? 'Auto-detected from your IP. '
            : ''}
        <Link to="/settings" className="text-primary-700 hover:underline">
          Change in settings
        </Link>
        .
      </p>

      {showRegionHint && (
        <div className="mb-4 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-900">
          You're previewing <strong>{region?.toUpperCase()}</strong>. Your saved region is{' '}
          <strong>{user?.region?.toUpperCase()}</strong>.
        </div>
      )}

      {loading && <div className="text-gray-500">Loading…</div>}
      {errorMessage && <div className="text-red-600">{errorMessage}</div>}

      {!loading && !errorMessage && items.length === 0 && (
        <div className="text-gray-500">
          {debouncedQ && category
            ? `No top podcasts in ${category} match "${debouncedQ}".`
            : debouncedQ
              ? `No top podcasts match "${debouncedQ}".`
              : category
                ? `No top podcasts in ${category} for this region.`
                : 'No top podcasts available for this region.'}
        </div>
      )}

      <ol className="space-y-2">
        {items.map((podcast) => {
          const status = adding[podcast.rss_url] ?? 'idle'
          const isResolving = !!resolving[podcast.rss_url]
          return (
            <li
              key={podcast.rss_url}
              onClick={() => handleOpen(podcast)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleOpen(podcast)
                }
              }}
              role="link"
              tabIndex={0}
              aria-busy={isResolving}
              aria-label={`Open ${podcast.name}`}
              className={`flex items-center gap-4 bg-white border border-gray-200 rounded-lg px-4 py-3 transition-colors hover:bg-gray-50 hover:border-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 cursor-pointer ${
                isResolving ? 'opacity-70' : ''
              }`}
            >
              <span className="w-10 text-right flex-shrink-0 flex items-center justify-end">
                {isResolving ? (
                  <span
                    className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-primary-600 border-t-transparent"
                    aria-label="Loading"
                  />
                ) : (
                  <span className="text-xl font-semibold tabular-nums text-gray-400">
                    {podcast.rank}
                  </span>
                )}
              </span>
              {podcast.image_url ? (
                <img
                  src={podcast.image_url}
                  alt=""
                  width={48}
                  height={48}
                  loading="lazy"
                  className="w-12 h-12 rounded-md object-cover flex-shrink-0 aspect-square bg-gray-100"
                />
              ) : (
                <div
                  className="w-12 h-12 rounded-md flex-shrink-0 aspect-square bg-gradient-to-br from-primary-100 to-secondary-100 flex items-center justify-center"
                  aria-hidden="true"
                >
                  <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                  </svg>
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-gray-900 truncate">{podcast.name}</div>
                <div className="text-sm text-gray-500 truncate">
                  {podcast.artist ?? 'Unknown artist'}
                  {podcast.category && (
                    <span className="ml-2 text-gray-400">· {podcast.category}</span>
                  )}
                </div>
              </div>
              <div
                className="flex items-center gap-2 flex-shrink-0"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                {podcast.apple_url && (
                  <a
                    href={podcast.apple_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-gray-500 hover:text-gray-900"
                  >
                    Apple
                  </a>
                )}
                {(() => {
                  const isFollowed = podcast.is_following || status === 'done'
                  const effectiveStatus: 'idle' | 'pending' | 'done' | 'error' = isFollowed
                    ? 'done'
                    : status
                  return (
                    <button
                      onClick={() => handleAdd(podcast)}
                      disabled={effectiveStatus === 'pending' || effectiveStatus === 'done'}
                      className={`px-3 py-1.5 rounded-md text-xs font-medium ${
                        effectiveStatus === 'done'
                          ? 'bg-green-100 text-green-800 cursor-default'
                          : effectiveStatus === 'error'
                            ? 'bg-red-100 text-red-800 hover:bg-red-200'
                            : 'bg-primary-900 text-white hover:bg-primary-800 disabled:opacity-50'
                      }`}
                    >
                      {effectiveStatus === 'pending'
                        ? 'Following…'
                        : effectiveStatus === 'done'
                          ? 'Following ✓'
                          : effectiveStatus === 'error'
                            ? 'Retry'
                            : 'Follow'}
                    </button>
                  )
                })()}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
