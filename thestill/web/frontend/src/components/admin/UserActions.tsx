import { useState } from 'react'
import type { AccountStatus, AdminUser } from '../../api/types'
import { useAdminToast } from '../../hooks/useAdminToast'
import {
  useLogoutEverywhere,
  useRemoveIntroducedFeeds,
  useSetAdminRole,
  useSetUserStatus,
} from '../../hooks/useAdmin'
import Button from '../Button'
import ConfirmDialog from '../ConfirmDialog'
import ReasonDialog from './ReasonDialog'

type Dialog = 'suspend' | 'ban' | 'reinstate' | 'logout' | 'remove-feeds' | 'admin' | null

/** Spec #104 §4.2 — the account actions, each behind a confirm dialog. */
export default function UserActions({ user, isSelf }: { user: AdminUser; isSelf: boolean }) {
  const toast = useAdminToast()
  const setStatus = useSetUserStatus()
  const logout = useLogoutEverywhere()
  const removeFeeds = useRemoveIntroducedFeeds()
  const setAdmin = useSetAdminRole()
  const [dialog, setDialog] = useState<Dialog>(null)
  const [alsoRemoveFeeds, setAlsoRemoveFeeds] = useState(false)
  const busy = setStatus.isPending || logout.isPending || removeFeeds.isPending || setAdmin.isPending
  const close = () => setDialog(null)
  const done = (message: string) => () => {
    close()
    toast.ok(message)
  }
  const changeStatus = (status: AccountStatus, reason: string, message: string) =>
    setStatus.mutate(
      { id: user.id, status, reason, remove_introduced_feeds: status === 'banned' && alsoRemoveFeeds },
      { onSuccess: done(message), onError: toast.fail },
    )

  return (
    <section className="bg-surface border border-hairline rounded-lg p-4">
      <h2 className="font-semibold text-ink mb-3">Actions</h2>
      {isSelf && <p className="text-sm text-muted mb-3">You can't change your own status or admin role.</p>}
      <div className="flex flex-wrap gap-2">
        {user.status === 'active' ? (
          <>
            <Button size="sm" variant="secondary" disabled={isSelf} onClick={() => setDialog('suspend')}>
              Suspend
            </Button>
            <Button size="sm" variant="danger" disabled={isSelf} onClick={() => setDialog('ban')}>
              Ban
            </Button>
          </>
        ) : (
          <Button size="sm" variant="primary" onClick={() => setDialog('reinstate')}>
            Reinstate
          </Button>
        )}
        <Button size="sm" variant="secondary" onClick={() => setDialog('logout')}>
          Sign out everywhere
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setDialog('remove-feeds')}>
          Remove feeds they brought in
        </Button>
        <Button size="sm" variant="ghost" disabled={isSelf} onClick={() => setDialog('admin')}>
          {user.is_admin ? 'Remove admin role' : 'Make admin'}
        </Button>
      </div>

      <ReasonDialog
        isOpen={dialog === 'suspend'}
        title="Suspend this account?"
        description="They're signed out everywhere and can't sign in. Their follows are kept, so reinstating restores the account exactly."
        confirmLabel="Suspend"
        busy={busy}
        onCancel={close}
        onConfirm={(reason) => changeStatus('suspended', reason, 'Account suspended')}
      />
      <ReasonDialog
        isOpen={dialog === 'ban'}
        title="Ban this account?"
        description="They're signed out and can't sign in, and their follows are moved aside, so feeds only they followed stop processing."
        confirmLabel="Ban"
        confirmVariant="danger"
        busy={busy}
        onCancel={close}
        onConfirm={(reason) => changeStatus('banned', reason, 'Account banned')}
      >
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={alsoRemoveFeeds} onChange={(e) => setAlsoRemoveFeeds(e.target.checked)} />
          Also remove feeds they brought in that nobody else follows
        </label>
      </ReasonDialog>
      <ReasonDialog
        isOpen={dialog === 'reinstate'}
        title="Reinstate this account?"
        description="They can sign in again. Follows a ban moved aside come back."
        confirmLabel="Reinstate"
        reasonRequired={false}
        busy={busy}
        onCancel={close}
        onConfirm={(reason) => changeStatus('active', reason, 'Account reinstated')}
      />
      <ConfirmDialog
        isOpen={dialog === 'logout'}
        title="Sign out everywhere?"
        message="Every session and their MCP connector token stop working. They can sign in again."
        confirmLabel="Sign out everywhere"
        busy={busy}
        onCancel={close}
        onConfirm={() => logout.mutate(user.id, { onSuccess: done('Signed out everywhere'), onError: toast.fail })}
      />
      <ConfirmDialog
        isOpen={dialog === 'remove-feeds'}
        title="Remove the feeds they brought in?"
        message="Their follow of every feed they brought in that no other active user follows is dropped, and those feeds' queued work is cancelled. Episodes already processed stay."
        confirmLabel="Remove feeds"
        confirmVariant="danger"
        busy={busy}
        onCancel={close}
        onConfirm={() =>
          removeFeeds.mutate(user.id, {
            onSuccess: (result) => {
              close()
              toast.ok(`Removed ${result.feeds_removed.length} feeds`)
            },
            onError: toast.fail,
          })
        }
      />
      <ConfirmDialog
        isOpen={dialog === 'admin'}
        title={user.is_admin ? 'Remove the admin role?' : 'Make this user an admin?'}
        message={
          user.is_admin
            ? 'They lose access to the admin pages and the operator API.'
            : 'They get the admin pages, including this console, and the operator API.'
        }
        confirmLabel={user.is_admin ? 'Remove admin role' : 'Make admin'}
        confirmVariant={user.is_admin ? 'danger' : 'primary'}
        busy={busy}
        onCancel={close}
        onConfirm={() =>
          setAdmin.mutate(
            { id: user.id, isAdmin: !user.is_admin },
            { onSuccess: done(user.is_admin ? 'Admin role removed' : 'Now an admin'), onError: toast.fail },
          )
        }
      />
    </section>
  )
}
