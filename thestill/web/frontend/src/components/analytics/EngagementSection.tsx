import type { AnalyticsReport } from '../../api/types'
import Panel from '../Panel'
import { formatPct, formatRatio } from '../../utils/analyticsFormat'

function Stat({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return (
    <div>
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-2xl font-semibold text-ink tabular-nums">{value}</dd>
      {detail ? <dd className="text-xs text-muted">{detail}</dd> : null}
    </div>
  )
}

/**
 * Activity in the window across everyone, not only new users. Averages and
 * per-briefing ratios, so diagnostic only (spec #111 §5).
 */
export default function EngagementSection({ engagement: e }: { engagement: AnalyticsReport['engagement'] }) {
  return (
    <Panel as="section" aria-labelledby="analytics-engagement" className="p-4 space-y-3">
      <div>
        <h2 id="analytics-engagement" className="text-section text-ink">
          Engagement
        </h2>
        <p className="text-sm text-muted">Diagnostics: these help explain a change; the Scorecard decides it.</p>
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="Active users" value={e.active_users} />
        <Stat
          label="Episodes read"
          value={e.reads}
          detail={`${formatRatio(e.reads_per_active_user_per_week)} per active user per week`}
        />
        <Stat
          label="Briefings opened"
          value={formatPct(e.opened_ratio)}
          detail={`${e.briefings_opened} of ${e.briefings_generated}`}
        />
        <Stat
          label="Follows"
          value={e.follows}
          detail={`${formatRatio(e.follows_per_active_user)} per active user`}
        />
      </dl>
    </Panel>
  )
}
