import type { AnalyticsReport } from '../../api/types'
import Panel from '../Panel'
import Bar from './Bar'
import PctCell from './PctCell'
import { formatHours, sourceLabel } from '../../utils/analyticsFormat'

/**
 * Activation funnel: one row per step, overall first, then one column per
 * signup source. Scrolls sideways on phones rather than squeezing columns.
 */
export default function ActivationSection({ activation }: { activation: AnalyticsReport['activation'] }) {
  const { overall, by_source: groups } = activation
  return (
    <Panel as="section" aria-labelledby="analytics-activation" className="p-4 space-y-3">
      <div>
        <h2 id="analytics-activation" className="text-section text-ink">
          Activation
        </h2>
        <p className="text-sm text-muted">
          New users in this window. Each step counts only users who signed up at least that long ago.
        </p>
      </div>
      <div className="overflow-x-auto -mx-4 px-4">
        <table className="w-full text-sm">
          <caption className="sr-only">Activation steps, overall and by signup source</caption>
          <thead>
            <tr className="text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">
                Step
              </th>
              <th scope="col" className="py-2 pr-4 font-medium whitespace-nowrap">
                All ({overall.signups})
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Median
              </th>
              {groups.map((g) => (
                <th key={g.source} scope="col" className="py-2 pr-4 font-medium whitespace-nowrap">
                  {sourceLabel(g.source)} ({g.signups})
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {overall.steps.map((step, i) => (
              <tr key={step.key} className="border-t border-hairline align-top">
                <th scope="row" className="py-2 pr-4 text-left font-normal text-ink min-w-40">
                  {step.label}
                </th>
                <td className="py-2 pr-4 min-w-32">
                  <PctCell numerator={step.reached} denominator={step.eligible} pct={step.pct} />
                  <div className="mt-1">
                    <Bar value={step.pct} />
                  </div>
                </td>
                <td className="py-2 pr-4 text-muted tabular-nums whitespace-nowrap">
                  {formatHours(step.median_hours)}
                </td>
                {groups.map((g) => {
                  const s = g.steps[i]
                  return (
                    <td key={g.source} className="py-2 pr-4">
                      <PctCell numerator={s.reached} denominator={s.eligible} pct={s.pct} />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
