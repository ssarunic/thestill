import type { AnalyticsMetricPoint, AnalyticsScorecard } from '../../api/types'
import Panel from '../Panel'
import PctCell from './PctCell'
import { formatPct, formatWeek, isSmallGroup } from '../../utils/analyticsFormat'

function describePoint(p: AnalyticsMetricPoint): string {
  const share = p.eligible ? `${formatPct(p.pct)} (${p.reached}/${p.eligible})` : 'not graded yet'
  return `Week of ${formatWeek(p.week_start)}: ${share}`
}

/** One bar per week, oldest first; ungraded weeks are a stub, small groups are greyed. */
function WeekBars({ points }: { points: AnalyticsMetricPoint[] }) {
  return (
    <div
      className="flex items-end gap-1 h-7"
      role="img"
      aria-label={`Last ${points.length} weeks. ${points.map(describePoint).join('. ')}`}
    >
      {points.map((p) => (
        <div
          key={p.week_start}
          title={describePoint(p)}
          className={`w-2 rounded-sm ${p.eligible ? 'bg-accent' : 'bg-hairline'} ${
            p.eligible && isSmallGroup(p.eligible) ? 'opacity-60' : ''
          }`}
          style={{ height: p.eligible ? `${Math.max(6, (p.pct ?? 0) * 100)}%` : '2px' }}
        />
      ))}
    </div>
  )
}

/**
 * Spec #111 — the four numbers that decide whether a change worked: each the
 * share of readers who cross a threshold, never an average. The latest value
 * is the newest week with anyone graded; the bars show the last weeks.
 */
export default function ScorecardSection({ scorecard }: { scorecard: AnalyticsScorecard }) {
  return (
    <Panel as="section" aria-labelledby="analytics-scorecard" className="p-4 space-y-4">
      <div>
        <h2 id="analytics-scorecard" className="text-section text-ink">
          Scorecard
        </h2>
        <p className="text-sm text-muted">
          Engagement is the main metric; activation is its early signal, week-4 retention the long-term check.
        </p>
      </div>
      <div className="overflow-x-auto -mx-4 px-4">
        <table className="w-full text-sm">
          <caption className="sr-only">Scorecard: each metric's newest graded week and its last weeks</caption>
          <thead>
            <tr className="text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">
                Metric
              </th>
              <th scope="col" className="py-2 font-medium whitespace-nowrap">
                Latest, then weekly
              </th>
            </tr>
          </thead>
          <tbody>
            {scorecard.metrics.map((metric) => {
              const graded = metric.points.filter((p) => p.eligible > 0)
              const latest = graded[graded.length - 1]
              return (
                <tr key={metric.key} className="border-t border-hairline align-top">
                  <th scope="row" className="py-2 pr-4 text-left font-normal">
                    <span className="text-ink font-medium">{metric.label}</span>{' '}
                    <span className="text-xs text-muted whitespace-nowrap">
                      {metric.readers === 'new' ? 'New readers' : 'Existing readers'}
                    </span>
                    <span className="block text-xs text-muted mt-0.5 max-w-sm">{metric.definition}</span>
                  </th>
                  <td className="py-2 space-y-1.5">
                    {latest ? (
                      <>
                        <PctCell numerator={latest.reached} denominator={latest.eligible} pct={latest.pct} />
                        <span className="block text-xs text-muted whitespace-nowrap">
                          week of {formatWeek(latest.week_start)}
                        </span>
                      </>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                    <WeekBars points={metric.points} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="space-y-1">
        <h3 className="text-sm font-medium text-ink">Guardrails</h3>
        <ul className="text-sm space-y-1">
          {scorecard.guardrails.map((g) => (
            <li key={g.key}>
              <span className="text-ink">{g.label}:</span>{' '}
              <span className="text-ink font-medium tabular-nums">{g.value ?? 'in PostHog'}</span>{' '}
              <span className="text-xs text-muted">({g.detail})</span>
            </li>
          ))}
        </ul>
      </div>
      <ul className="text-xs text-muted space-y-1 list-disc pl-4">
        <li>
          {scorecard.opted_out} {scorecard.opted_out === 1 ? 'reader has' : 'readers have'} usage analytics off.
          Connector days are recorded{' '}
          {scorecard.connector_days_since ? `from ${scorecard.connector_days_since}` : 'from the first call (none yet)'}.
        </li>
        {scorecard.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </Panel>
  )
}
