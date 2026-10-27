import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { approveOAuthPending, denyOAuthPending } from '../api/client'
import type { McpTokenScope } from '../api/types'
import McpScopeForm from '../components/McpScopeForm'
import { useAuth } from '../contexts/AuthContext'
import { useOAuthPending } from '../hooks/useApi'

// Spec #109 — the OAuth consent screen. An MCP client (Claude, Claude Code,
// ChatGPT…) sent the reader to /authorize, which parked the request and
// redirected here with its id in the fragment (never in a log or Referer).
// The session cookie is SameSite=strict, so it rode none of that cross-site
// navigation; everything here goes through same-origin API calls instead.
// A signed-out reader never gets here: the route sits behind
// ProtectedRoute, which sends them through /login and back, fragment
// included.

function requestIdFrom(hash: string): string | null {
  return new URLSearchParams(hash.replace(/^#/, '')).get('request')
}

export default function OAuthConsent() {
  const { hash } = useLocation()
  const { isAdmin } = useAuth()
  const requestId = requestIdFrom(hash)
  const { data, isLoading, error } = useOAuthPending(requestId)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  async function decide(action: () => Promise<{ redirect: string }>) {
    setBusy(true)
    setFailure(null)
    try {
      const { redirect } = await action()
      // A full navigation to the app's own callback, not a router push.
      window.location.assign(redirect)
    } catch (err) {
      setBusy(false)
      setFailure(err instanceof Error ? err.message : 'Something went wrong')
    }
  }

  const appName = data?.client_name || 'An app'

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4 py-10">
      <div className="bg-white border border-gray-200 rounded-xl shadow-sm max-w-md w-full p-6 space-y-5">
        {(!requestId || error) && (
          <div className="space-y-2">
            <h1 className="text-lg font-semibold text-gray-900">This request has expired</h1>
            <p className="text-sm text-gray-600">
              It was already answered, or it waited longer than ten minutes. Go back to the app and connect again.
            </p>
          </div>
        )}
        {requestId && isLoading && <p className="text-sm text-gray-500">Loading…</p>}
        {data && (
          <>
            <div className="space-y-2">
              <h1 className="text-lg font-semibold text-gray-900">
                {appName} wants to connect to your thestill account
              </h1>
              <p className="text-sm text-gray-600">After you decide, you go back to</p>
              <p data-testid="redirect-host" className="text-2xl font-semibold text-gray-900 break-all">
                {data.redirect_host}
              </p>
              {data.loopback_only && (
                <p role="note" className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md p-3">
                  This is a program on this computer. Approve only if you just started connecting from an app you
                  trust, such as Claude Code.
                </p>
              )}
            </div>
            <McpScopeForm
              isAdmin={isAdmin}
              initial={['read']}
              offer={data.requested_scopes as McpTokenScope[]}
              legend={`What ${appName} may do`}
              busy={busy}
              submitLabel="Approve"
              cancelLabel="Deny"
              onSubmit={(scopes) => decide(() => approveOAuthPending(requestId as string, scopes))}
              onCancel={() => decide(() => denyOAuthPending(requestId as string))}
            />
            {data.offline_access && (
              <p className="text-xs text-gray-500">
                Stays connected until you disconnect it in Settings → Claude and other AI apps.
              </p>
            )}
            <p className="text-xs text-gray-500">
              The app sees what you can see in thestill, within what you approve here. thestill never sees your
              questions or the app’s answers.
            </p>
          </>
        )}
        {failure && <p className="text-sm text-red-600">{failure}</p>}
      </div>
    </main>
  )
}
