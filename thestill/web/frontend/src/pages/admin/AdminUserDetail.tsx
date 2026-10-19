import { Link, useNavigate, useParams } from 'react-router-dom'
import QuotaOverridesForm from '../../components/admin/QuotaOverridesForm'
import StatusBadge from '../../components/admin/StatusBadge'
import UserActions from '../../components/admin/UserActions'
import { FollowsList, HistoryList, UsageTable } from '../../components/admin/UserSections'
import { useAuth } from '../../contexts/AuthContext'
import { useAdminUser } from '../../hooks/useAdmin'
import { formatWhen as when } from '../../utils/adminFormat'
import MultiUserOnly from './MultiUserOnly'

/** Spec #104 §4.2 — one account: identity, usage, follows, actions, history. */
export default function AdminUserDetailPage() {
  const { userId = '' } = useParams()
  const { isMultiUser } = useAuth()
  if (!isMultiUser) return <MultiUserOnly title="User" />
  // Keyed by user so drafts (quota form, dialogs) never carry over.
  return <AdminUserDetail key={userId} userId={userId} />
}

function AdminUserDetail({ userId }: { userId: string }) {
  const { user: me } = useAuth()
  const navigate = useNavigate()
  const { data, isLoading, error } = useAdminUser(userId)

  // Back to the list as it was (filters, page, scroll), when we came from it.
  const backToList = (event: React.MouseEvent) => {
    if ((window.history.state?.idx ?? 0) > 0) {
      event.preventDefault()
      navigate(-1)
    }
  }
  const back = (
    <Link to="/admin/users" onClick={backToList} className="text-sm text-muted hover:underline">
      ← Users
    </Link>
  )

  if (error) return <div className="space-y-4">{back}<p className="text-red-600 text-sm">{error.message}</p></div>
  if (isLoading || !data) return <div className="animate-pulse h-64 bg-surface border border-hairline rounded-lg" />

  const { user } = data
  return (
    <div className="space-y-6">
      {back}
      <header className="flex flex-wrap items-start gap-4">
        {user.picture && <img src={user.picture} alt="" className="w-14 h-14 rounded-full" />}
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-ink break-all">{user.email}</h1>
          <p className="text-muted">
            {user.name ?? '—'} · joined {when(user.created_at)} · last sign-in {when(user.last_login_at)}
            {user.signup_source && ` · from ${user.signup_source}`}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <StatusBadge status={user.status} />
            {user.is_admin && (
              <span className="rounded-full border border-hairline px-2 py-0.5 text-xs font-medium">admin</span>
            )}
            {user.status !== 'active' && (
              <span className="text-muted">
                {user.status_reason ? `“${user.status_reason}”` : 'No reason given'} · {when(user.status_changed_at)}
              </span>
            )}
          </div>
        </div>
      </header>
      <UserActions user={user} isSelf={me?.id === user.id} />
      <UsageTable usage24h={data.usage_24h} usage7d={data.usage_7d} daily={data.daily_7d} />
      <QuotaOverridesForm userId={user.id} limits={data.quota_limits} overrides={data.quota_overrides} />
      <FollowsList follows={data.follows} />
      <HistoryList history={data.history} />
    </div>
  )
}
