import { Link } from 'react-router-dom'
import type { AdminAuditEntry, AdminFollow } from '../../api/types'
import { describeAuditEntry } from '../../utils/adminFormat'
import Sparkline from './Sparkline'

// Display names for the usage ledger's kinds (services/abuse ledger).
const USAGE_KINDS: { kind: string; label: string }[] = [
  { kind: 'feed_introduced', label: 'Feeds brought in' },
  { kind: 'follow', label: 'Follows' },
  { kind: 'import', label: 'Imports' },
  { kind: 'translation', label: 'Translations' },
  { kind: 'narration', label: 'Narrations' },
  { kind: 'quota_exceeded', label: 'Quota hits' },
]

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

export function UsageTable({
  usage24h,
  usage7d,
  daily,
}: {
  usage24h: Record<string, number>
  usage7d: Record<string, number>
  daily: Record<string, number[]>
}) {
  return (
    <section className="bg-surface border border-hairline rounded-lg p-4">
      <h2 className="font-semibold text-ink mb-3">Usage</h2>
      <table className="text-sm">
        <thead className="text-muted text-left">
          <tr>
            <th className="pr-6 py-1 font-medium" />
            <th className="pr-6 py-1 font-medium text-right">24h</th>
            <th className="pr-6 py-1 font-medium text-right">7 days</th>
            <th className="py-1 font-medium">Daily</th>
          </tr>
        </thead>
        <tbody>
          {USAGE_KINDS.map(({ kind, label }) => (
            <tr key={kind}>
              <td className="pr-6 py-1 text-ink">{label}</td>
              <td className="pr-6 py-1 text-right tabular-nums">{usage24h[kind] ?? 0}</td>
              <td className="pr-6 py-1 text-right tabular-nums">{usage7d[kind] ?? 0}</td>
              <td className="py-1">
                <Sparkline values={daily[kind] ?? Array(7).fill(0)} label={label} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function FollowsList({ follows }: { follows: AdminFollow[] }) {
  return (
    <section className="bg-surface border border-hairline rounded-lg p-4">
      <h2 className="font-semibold text-ink mb-3">Follows ({follows.length})</h2>
      {follows.length === 0 ? (
        <p className="text-sm text-muted">None.</p>
      ) : (
        <ul className="divide-y divide-hairline text-sm">
          {follows.map((follow) => (
            <li key={follow.podcast_id} className="py-2 flex flex-wrap items-center gap-2">
              <Link to={`/podcasts/${follow.slug}`} className="text-ink hover:underline">
                {follow.title}
              </Link>
              {follow.introduced && <span className="text-xs rounded bg-amber-50 text-amber-800 px-1.5">brought in</span>}
              {follow.suspended && <span className="text-xs rounded bg-red-50 text-red-800 px-1.5">moved aside</span>}
              <span className="text-muted text-xs">
                {follow.other_followers} other follower{follow.other_followers === 1 ? '' : 's'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function HistoryList({ history }: { history: AdminAuditEntry[] }) {
  return (
    <section className="bg-surface border border-hairline rounded-lg p-4">
      <h2 className="font-semibold text-ink mb-3">History</h2>
      {history.length === 0 ? (
        <p className="text-sm text-muted">No admin actions yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {history.map((entry) => (
            <li key={entry.id}>
              <span className="text-muted tabular-nums">{when(entry.created_at)}</span>{' '}
              <span className="text-ink">{describeAuditEntry(entry)}</span> <span className="text-muted">({entry.actor})</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
