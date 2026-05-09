import { Link } from 'react-router-dom'
import { useNarrationDashboardStats } from '../hooks/useApi'

function formatRuntime(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return '–'
  const total = Math.round(seconds)
  if (total < 60) return `${total}s`
  const m = Math.floor(total / 60)
  const s = total % 60
  return s === 0 ? `${m}m` : `${m}m ${s.toString().padStart(2, '0')}s`
}

function formatLatency(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return '–'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fallbackRateClass(rate: number): string {
  if (rate < 0.05) return 'text-emerald-700 bg-emerald-50'
  if (rate < 0.15) return 'text-amber-700 bg-amber-50'
  return 'text-red-700 bg-red-50'
}

function digestIdFromNarrationId(narrationId: string): string | null {
  // ``<digest_id>-<slug>`` — the slug is one of the duration presets,
  // so we strip from the rightmost ``-`` and let the dashboard tile
  // link to the digest viewer. Safe enough for v1; the API also
  // surfaces a ``digest_id`` field if we ever need stricter parsing.
  const lastDash = narrationId.lastIndexOf('-')
  return lastDash > 0 ? narrationId.slice(0, lastDash) : null
}

export default function NarrationStatsTile() {
  const { data, isLoading, error } = useNarrationDashboardStats()

  if (error) {
    // Silent on the dashboard — narration is optional infrastructure.
    return null
  }

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-5 bg-gray-200 rounded w-1/3" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-12 bg-gray-100 rounded" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (!data || data.total_runs === 0) {
    return null
  }

  const latestDigestId = data.latest
    ? digestIdFromNarrationId(data.latest.narration_id)
    : null

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Narration health</h2>
          <p className="text-sm text-gray-500">Spec #33 narrated digest, on-disk aggregates</p>
        </div>
        {latestDigestId && (
          <Link
            to={`/digests/${latestDigestId}`}
            className="text-sm text-primary-600 hover:underline"
          >
            View latest →
          </Link>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">Runs</div>
          <div className="text-2xl font-semibold text-gray-900">{data.total_runs}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">Fallback rate</div>
          <div
            className={`inline-block mt-1 px-2 py-0.5 rounded text-sm font-semibold ${fallbackRateClass(
              data.fallback_rate,
            )}`}
          >
            {(data.fallback_rate * 100).toFixed(1)}%
          </div>
          <div className="text-xs text-gray-400 mt-0.5">
            {data.fallback_count} of {data.total_runs}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">Avg runtime</div>
          <div className="text-2xl font-semibold text-gray-900">
            {formatRuntime(data.avg_actual_duration_seconds)}
          </div>
          <div className="text-xs text-gray-400 mt-0.5">
            target {formatRuntime(data.avg_target_duration_seconds)}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">Avg latency</div>
          <div className="text-2xl font-semibold text-gray-900">
            {formatLatency(data.avg_latency_ms)}
          </div>
        </div>
      </div>

      {data.latest && data.latest.mode === 'fallback' && (
        <p className="mt-4 text-xs text-amber-700">
          Latest run fell back to link-index
          {data.latest.fallback_reason ? ` (${data.latest.fallback_reason})` : ''}.
        </p>
      )}
    </div>
  )
}
