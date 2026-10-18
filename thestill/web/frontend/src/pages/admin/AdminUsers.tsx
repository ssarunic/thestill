import { Link } from 'react-router-dom'
import type { AccountStatus, AdminUserSort } from '../../api/types'
import Pager from '../../components/admin/Pager'
import StatusBadge from '../../components/admin/StatusBadge'
import { useAuth } from '../../contexts/AuthContext'
import { useAdminUsers } from '../../hooks/useAdmin'
import { useDebouncedSearchParam } from '../../hooks/useDebouncedSearchParam'
import { useUrlParams } from '../../hooks/useUrlParams'
import MultiUserOnly from './MultiUserOnly'

const PAGE_SIZE = 50
const RESET_ON_SEARCH = ['page'] as const
const STATUSES: (AccountStatus | '')[] = ['', 'active', 'suspended', 'banned']
const SORTS: { value: AdminUserSort; label: string }[] = [
  { value: 'attention', label: 'Needs a look' },
  { value: 'created', label: 'Newest' },
  { value: 'last-login', label: 'Last sign-in' },
  { value: 'follows', label: 'Most follows' },
  { value: 'email', label: 'Email' },
]

function formatDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString() : '—'
}

/**
 * Spec #104 §4.1 — every account with its activity. Filters, sort and page
 * live in the URL, so Back restores them (and, via the app-wide scroll
 * restoration, the scroll position).
 */
export default function AdminUsers() {
  const { isMultiUser } = useAuth()
  const params = useUrlParams()
  const search = useDebouncedSearchParam('q', 250, RESET_ON_SEARCH)
  const status = params.get('status') as AccountStatus | ''
  const sort = params.get('sort', 'attention') as AdminUserSort
  const { data, isLoading, error } = useAdminUsers(
    {
      q: search.debouncedValue || undefined,
      status: status || undefined,
      sort,
      limit: PAGE_SIZE,
      offset: params.page * PAGE_SIZE,
    },
    isMultiUser,
  )

  if (!isMultiUser) return <MultiUserOnly title="Users" />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-ink">Users</h1>
        <p className="text-muted mt-1">Who is doing what. Accounts with quota hits or many new feeds come first.</p>
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <input
          type="search"
          value={search.value}
          onChange={(e) => search.setValue(e.target.value)}
          placeholder="Search email or name"
          aria-label="Search users"
          className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink w-64"
        />
        <select
          value={status}
          onChange={(e) => params.set('status', e.target.value)}
          aria-label="Status"
          className="rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || 'Any status'}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(e) => params.set('sort', e.target.value, 'attention')}
          aria-label="Sort"
          className="rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <p className="text-red-600 text-sm">{error.message}</p>
      ) : isLoading || !data ? (
        <div className="animate-pulse h-64 bg-surface border border-hairline rounded-lg" aria-busy="true" />
      ) : data.users.length === 0 ? (
        <p className="text-muted text-sm">No users match.</p>
      ) : (
        <div className="overflow-x-auto bg-surface border border-hairline rounded-lg">
          <table className="min-w-full text-sm">
            <thead className="text-left text-muted border-b border-hairline">
              <tr>
                <th className="px-3 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Joined</th>
                <th className="px-3 py-2 font-medium">Last sign-in</th>
                <th className="px-3 py-2 font-medium text-right">Follows</th>
                <th className="px-3 py-2 font-medium text-right" title="Last 24 hours / all time">
                  Feeds brought in
                </th>
                <th className="px-3 py-2 font-medium text-right">Imports 24h</th>
                <th className="px-3 py-2 font-medium text-right">Translations 24h</th>
                <th className="px-3 py-2 font-medium text-right">Quota hits 24h</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((user) => (
                <tr key={user.id} className="border-b border-hairline last:border-0">
                  <td className="px-3 py-2">
                    <Link to={`/admin/users/${user.id}`} className="text-ink font-medium hover:underline">
                      {user.email}
                    </Link>
                    <div className="text-xs text-muted">
                      {user.name ?? '—'}
                      {user.is_admin && ' · admin'}
                      {user.signup_source && ` · from ${user.signup_source}`}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={user.status} />
                  </td>
                  <td className="px-3 py-2 text-muted">{formatDate(user.created_at)}</td>
                  <td className="px-3 py-2 text-muted">{formatDate(user.last_login_at)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{user.follows}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {user.feeds_brought_in_24h} / {user.feeds_brought_in_total}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{user.imports_24h}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{user.translations_24h}</td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${user.quota_hits_24h ? 'text-red-700 font-semibold' : ''}`}
                  >
                    {user.quota_hits_24h}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <Pager page={params.page} pageSize={PAGE_SIZE} total={data.total} hasMore={data.has_more} onPage={params.setPage} />
      )}
    </div>
  )
}
