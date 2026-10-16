import type { AnalyticsReport } from '../../api/types'
import Panel from '../Panel'
import Bar from './Bar'
import { sourceLabel } from '../../utils/analyticsFormat'

/** Signups in the window: a per-day strip and the split by signup source. */
export default function SignupsSection({ signups }: { signups: AnalyticsReport['signups'] }) {
  const peak = Math.max(1, ...signups.days.map((d) => d.total))
  const sources = Object.entries(signups.by_source).sort((a, b) => b[1] - a[1])
  return (
    <Panel as="section" aria-labelledby="analytics-signups" className="p-4 space-y-4">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="analytics-signups" className="text-section text-ink">
          Signups
        </h2>
        <p className="text-2xl font-semibold text-ink tabular-nums">{signups.total}</p>
      </div>

      <div
        className="flex items-end gap-px h-12"
        role="img"
        aria-label={`Signups per day: ${signups.total} in ${signups.days.length} days, at most ${peak} in one day`}
      >
        {signups.days.map((d) => (
          <div
            key={d.day}
            className="flex-1 bg-accent/80 rounded-t-sm min-h-px"
            style={{ height: `${(d.total / peak) * 100}%` }}
            title={`${d.day}: ${d.total}`}
          />
        ))}
      </div>

      {sources.length === 0 ? (
        <p className="text-sm text-muted">No signups in this window.</p>
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Signups by the page they came from</caption>
          <tbody>
            {sources.map(([source, n]) => (
              <tr key={source} className="border-t border-hairline first:border-t-0">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink whitespace-nowrap">
                  {sourceLabel(source)}
                </th>
                <td className="py-1.5 pr-3 w-full">
                  <Bar value={n / signups.total} />
                </td>
                <td className="py-1.5 text-right tabular-nums text-ink">{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
