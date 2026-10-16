import type { AnalyticsReport } from '../../api/types'
import Panel from '../Panel'
import PctCell from './PctCell'
import { isSmallGroup } from '../../utils/analyticsFormat'

function formatWeek(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/**
 * Weekly cohort retention: the last 12 signup weeks (independent of the
 * range) by activity week after each user's own signup. A cell stays blank
 * until at least one member's week has fully passed.
 */
export default function RetentionSection({ cohorts }: { cohorts: AnalyticsReport['cohorts'] }) {
  const rows = [...cohorts.rows].reverse() // newest first
  return (
    <Panel as="section" aria-labelledby="analytics-retention" className="p-4 space-y-3">
      <div>
        <h2 id="analytics-retention" className="text-section text-ink">
          Retention
        </h2>
        <p className="text-sm text-muted">
          Share of each signup week active in week N after signing up. Always the last {cohorts.rows.length} weeks.
        </p>
      </div>
      <div className="overflow-x-auto -mx-4 px-4" data-testid="cohort-scroll">
        <table className="w-full text-sm">
          <caption className="sr-only">Share of each signup week active in later weeks</caption>
          <thead>
            <tr className="text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium whitespace-nowrap sticky left-0 bg-surface">
                Week of
              </th>
              <th scope="col" className="py-2 pr-4 font-medium text-right">
                Users
              </th>
              {cohorts.weeks.map((w) => (
                <th key={w} scope="col" className="py-2 pr-4 font-medium whitespace-nowrap">
                  Week {w}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.week_start}
                className={`border-t border-hairline ${row.size > 0 && isSmallGroup(row.size) ? 'opacity-60' : ''}`}
              >
                <th
                  scope="row"
                  className="py-2 pr-4 text-left font-normal text-ink whitespace-nowrap sticky left-0 bg-surface"
                >
                  {formatWeek(row.week_start)}
                </th>
                <td className="py-2 pr-4 text-right tabular-nums text-ink">{row.size}</td>
                {row.cells.map((cell) => (
                  <td key={cell.week} className="py-2 pr-4">
                    {cell.eligible > 0 ? (
                      <PctCell numerator={cell.active} denominator={cell.eligible} pct={cell.pct} />
                    ) : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
