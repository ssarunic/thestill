import { useSearchParams } from 'react-router-dom'
import { useAnalytics } from '../hooks/useApi'
import type { AnalyticsRange } from '../api/types'
import SegmentedControl from '../components/SegmentedControl'
import SignupsSection from '../components/analytics/SignupsSection'
import ActivationSection from '../components/analytics/ActivationSection'
import RetentionSection from '../components/analytics/RetentionSection'
import EngagementSection from '../components/analytics/EngagementSection'
import { DEFAULT_RANGE, RANGES, parseRange } from '../utils/analyticsFormat'

/**
 * Spec #95 Layer A — product analytics read from product tables: signups,
 * activation, cohort retention, engagement. Admin-only (AdminRoute + the
 * server's require_admin); aggregates only, no per-user rows.
 */
export default function Analytics() {
  // The range lives in the URL so Back restores it (app-wide convention).
  const [searchParams, setSearchParams] = useSearchParams()
  const range = parseRange(searchParams.get('range'))
  const setRange = (next: AnalyticsRange) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        // The default range keeps the URL clean.
        if (next === DEFAULT_RANGE) params.delete('range')
        else params.set('range', next)
        return params
      },
      { replace: true },
    )
  }
  const { data, isLoading, error } = useAnalytics(range)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-ink">Analytics</h1>
          <p className="text-muted mt-1">Signups, activation and retention from product data</p>
        </div>
        {data?.available !== false && (
          <SegmentedControl options={RANGES} value={range} onChange={setRange} ariaLabel="Date range" />
        )}
      </div>

      {error ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto text-center">
          <h2 className="text-red-700 font-medium mb-2">Error loading analytics</h2>
          <p className="text-red-600 text-sm">{error.message}</p>
        </div>
      ) : isLoading || !data ? (
        <div className="space-y-4" aria-busy="true">
          {[1, 2, 3].map((i) => (
            <div key={i} className="animate-pulse h-40 bg-surface border border-hairline rounded-lg" />
          ))}
        </div>
      ) : !data.available ? (
        <div className="bg-surface border border-hairline rounded-lg p-6 max-w-md mx-auto text-center">
          <h2 className="text-ink font-medium mb-2">Analytics is off</h2>
          <p className="text-muted text-sm">
            Product analytics needs multi-user mode on the Postgres backend.
          </p>
        </div>
      ) : (
        <>
          <SignupsSection signups={data.signups} />
          <ActivationSection activation={data.activation} />
          <RetentionSection cohorts={data.cohorts} />
          <EngagementSection engagement={data.engagement} />
          <ul className="text-xs text-muted space-y-1 list-disc pl-4">
            {data.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
