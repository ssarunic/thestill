import { Link } from 'react-router-dom'
import Pager from '../../components/admin/Pager'
import { useAuth } from '../../contexts/AuthContext'
import { useAdminAudit } from '../../hooks/useAdmin'
import { useUrlParams } from '../../hooks/useUrlParams'
import { describeAuditEntry } from '../../utils/adminFormat'
import MultiUserOnly from './MultiUserOnly'

const PAGE_SIZE = 50

/** Spec #104 §4.4 — every admin action, newest first. Filters live in the URL. */
export default function AdminAudit() {
  const { isMultiUser } = useAuth()
  const params = useUrlParams()
  const action = params.get('action')
  const target = params.get('target')
  const { data, isLoading, error } = useAdminAudit(
    { action: action || undefined, target_user_id: target || undefined, limit: PAGE_SIZE, offset: params.page * PAGE_SIZE },
    isMultiUser,
  )

  if (!isMultiUser) return <MultiUserOnly title="Audit log" />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-ink">Audit log</h1>
        <p className="text-muted mt-1">Every admin action, from the console, the CLI or automation. It can't be edited.</p>
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <select
          value={action}
          onChange={(e) => params.set('action', e.target.value)}
          aria-label="Action"
          className="rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
        >
          <option value="">All actions</option>
          {/* The server's list, so a new action needs no frontend change. */}
          {(data?.actions ?? (action ? [action] : [])).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        {target && (
          <button type="button" className="text-sm text-accent" onClick={() => params.set('target', '')}>
            Clear user filter ✕
          </button>
        )}
      </div>
      {error ? (
        <p className="text-red-600 text-sm">{error.message}</p>
      ) : isLoading || !data ? (
        <div className="animate-pulse h-64 bg-surface border border-hairline rounded-lg" aria-busy="true" />
      ) : data.entries.length === 0 ? (
        <p className="text-muted text-sm">Nothing logged yet.</p>
      ) : (
        <ul className="bg-surface border border-hairline rounded-lg divide-y divide-hairline text-sm">
          {data.entries.map((entry) => (
            <li key={entry.id} className="px-4 py-2">
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                <span className="text-muted tabular-nums">{new Date(entry.created_at).toLocaleString()}</span>
                <span className="font-medium text-ink">{entry.action}</span>
                {entry.target_user_id && (
                  <Link to={`/admin/users/${entry.target_user_id}`} className="text-accent hover:underline">
                    {entry.target_email ?? entry.target_user_id}
                  </Link>
                )}
                <span className="text-muted">
                  by {entry.actor === 'web' ? (entry.actor_email ?? 'an admin') : entry.actor}
                  {typeof entry.details.actor_name === 'string' && ` (${entry.details.actor_name})`}
                </span>
              </div>
              <div className="text-ink">{describeAuditEntry(entry)}</div>
            </li>
          ))}
        </ul>
      )}
      {data && (
        <Pager
          page={params.page}
          pageSize={PAGE_SIZE}
          total={data.total}
          hasMore={data.has_more}
          onPage={params.setPage}
          previousLabel="← Newer"
          nextLabel="Older →"
        />
      )}
    </div>
  )
}
