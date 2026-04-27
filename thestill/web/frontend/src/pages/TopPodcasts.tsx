import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { addPodcast, getTopPodcasts } from '../api/client'
import type { TopPodcast } from '../api/types'
import { flagFor } from '../utils/regions'

export default function TopPodcasts() {
  const { user } = useAuth()
  const [region, setRegion] = useState<string | null>(null)
  const [available, setAvailable] = useState<string[]>([])
  const [items, setItems] = useState<TopPodcast[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState<Record<string, 'idle' | 'pending' | 'done' | 'error'>>({})

  const requestedRegion = region ?? undefined

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getTopPodcasts(requestedRegion, 500)
      .then((res) => {
        if (cancelled) return
        setItems(res.top_podcasts)
        setAvailable(res.available_regions)
        // Trust the server's resolved region — it may differ from what we asked for.
        setRegion(res.region)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load top podcasts')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [requestedRegion])

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

  return (
    <div className="max-w-4xl">
      <div className="flex items-baseline justify-between mb-2 gap-4 flex-wrap">
        <h1 className="text-2xl font-bold text-gray-900">{headerLabel}</h1>
        <label className="text-sm text-gray-600 flex items-center gap-2">
          <span>Region</span>
          <select
            value={region ?? ''}
            onChange={(e) => setRegion(e.target.value || null)}
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
      {error && <div className="text-red-600">{error}</div>}

      {!loading && !error && items.length === 0 && (
        <div className="text-gray-500">No top podcasts available for this region.</div>
      )}

      <ol className="space-y-2">
        {items.map((podcast) => {
          const status = adding[podcast.rss_url] ?? 'idle'
          return (
            <li
              key={podcast.rss_url}
              className="flex items-center gap-4 bg-white border border-gray-200 rounded-lg px-4 py-3"
            >
              <span className="w-10 text-right text-xl font-semibold tabular-nums text-gray-400 flex-shrink-0">
                {podcast.rank}
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium text-gray-900 truncate">{podcast.name}</div>
                <div className="text-sm text-gray-500 truncate">
                  {podcast.artist ?? 'Unknown artist'}
                  {podcast.category && (
                    <span className="ml-2 text-gray-400">· {podcast.category}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
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
                <button
                  onClick={() => handleAdd(podcast)}
                  disabled={status === 'pending' || status === 'done'}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium ${
                    status === 'done'
                      ? 'bg-green-100 text-green-800 cursor-default'
                      : status === 'error'
                        ? 'bg-red-100 text-red-800 hover:bg-red-200'
                        : 'bg-primary-900 text-white hover:bg-primary-800 disabled:opacity-50'
                  }`}
                >
                  {status === 'pending'
                    ? 'Adding…'
                    : status === 'done'
                      ? 'Queued'
                      : status === 'error'
                        ? 'Retry'
                        : 'Add'}
                </button>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
