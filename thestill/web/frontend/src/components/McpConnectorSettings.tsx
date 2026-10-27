import { useState } from 'react'
import Button from './Button'
import ConfirmDialog from './ConfirmDialog'
import McpScopeForm from './McpScopeForm'
import { SCOPE_COPY } from '../constants/mcpScopes'
import { useToast } from './Toast'
import { useAuth } from '../contexts/AuthContext'
import {
  useCreateOrRotateMcpToken,
  useDisconnectOAuthGrant,
  useMcpToken,
  useOAuthGrants,
  useRevokeMcpToken,
} from '../hooks/useApi'
import type { McpTokenInfo, McpTokenScope, OAuthGrant } from '../api/types'

// Per-user Claude connector card (spec #78 Phase 2, spec #109).
//
// With OAuth on (spec #109) the card leads with the one address every
// reader pastes into Claude, lists the apps connected through it, and keeps
// the personal URL under Advanced for clients that cannot do OAuth. With
// OAuth off it is the personal-URL card alone.
//
// The personal URL *is* a credential (equivalent to this user's web
// session, narrowed by scopes), so the plaintext is shown exactly once —
// in a modal right after Create/Rotate — and never again: the server
// stores only a hash. There is no Reveal; a user who lost the URL
// rotates. Rotate, Revoke and Disconnect confirm through ConfirmDialog.

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function OneTimeUrlModal({ url, onClose }: { url: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
    } catch {
      // Clipboard unavailable (http, permissions): the URL is visible below
      // for manual selection.
    }
  }
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-url-title"
        className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="mcp-url-title" className="text-lg font-semibold text-gray-900">
          Your connector URL
        </h2>
        <p className="text-sm text-gray-600">
          Copy it now and paste it into claude.ai → Settings → Connectors → “Add custom connector”. It will
          not be shown again; if you lose it, rotate.
        </p>
        <code
          data-testid="mcp-url"
          className="block bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-xs text-gray-800 overflow-x-auto whitespace-nowrap select-all"
        >
          {url}
        </code>
        <div className="flex justify-end gap-2">
          <Button size="sm" onClick={copy}>
            {copied ? 'Copied' : 'Copy URL'}
          </Button>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  )
}

function StateBadge({ state }: { state: McpTokenInfo['state'] }) {
  const tone =
    state === 'active'
      ? 'bg-green-50 text-green-700 border-green-200'
      : state === 'expiring'
        ? 'bg-amber-50 text-amber-700 border-amber-200'
        : 'bg-gray-50 text-gray-600 border-gray-200'
  return <span className={`inline-block text-xs px-2 py-0.5 rounded-full border ${tone}`}>{state}</span>
}

function PersonalUrlSection({ data }: { data: McpTokenInfo }) {
  const { isAdmin } = useAuth()
  const { showToast } = useToast()
  const mint = useCreateOrRotateMcpToken()
  const revoke = useRevokeMcpToken()
  const [mode, setMode] = useState<'idle' | 'create' | 'rotate'>('idle')
  const [confirming, setConfirming] = useState<'rotate' | 'revoke' | null>(null)
  const [pendingScopes, setPendingScopes] = useState<McpTokenScope[] | null>(null)
  const [freshUrl, setFreshUrl] = useState<string | null>(null)

  const busy = mint.isPending || revoke.isPending

  async function doMint(scopes: McpTokenScope[]) {
    try {
      const res = await mint.mutateAsync(scopes)
      setFreshUrl(res.url)
      setMode('idle')
      setConfirming(null)
      setPendingScopes(null)
      showToast(data.state === 'active' || data.state === 'expiring' ? 'Connector rotated' : 'Connector created', 'success')
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to create connector', 'error')
    }
  }

  async function doRevoke() {
    try {
      await revoke.mutateAsync()
      setConfirming(null)
      showToast('Connector revoked', 'success')
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to revoke connector', 'error')
    }
  }

  const live = data.state === 'active' || data.state === 'expiring'

  return (
    <div className="space-y-4">
      {!data.enabled && (
        <div className="text-sm text-gray-600 space-y-2">
          <p>Remote MCP is disabled on this server. An operator turned it off with:</p>
          <pre className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs overflow-x-auto">
            {'MCP_HTTP_ENABLED=false'}
          </pre>
        </div>
      )}

      {data.enabled && !live && mode === 'idle' && (
        <div className="space-y-3 text-sm text-gray-600">
          {data.state === 'revoked' && <p>Revoked on {formatWhen(data.revoked_at)}.</p>}
          {data.state === 'expired' && <p>Expired on {formatWhen(data.expires_at)}.</p>}
          {data.state === 'none' && <p>No connector yet.</p>}
          <Button size="sm" onClick={() => setMode('create')}>
            Create connector URL
          </Button>
        </div>
      )}

      {data.enabled && mode === 'create' && (
        <McpScopeForm
          isAdmin={isAdmin}
          initial={['read']}
          busy={busy}
          submitLabel="Create"
          onSubmit={doMint}
          onCancel={() => setMode('idle')}
        />
      )}

      {data.enabled && live && mode === 'idle' && (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <code
              data-testid="mcp-url-masked"
              className="flex-1 bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-xs text-gray-800 overflow-x-auto whitespace-nowrap"
            >
              …/mcp/{data.prefix}••••••••
            </code>
            <StateBadge state={data.state} />
          </div>
          {data.state === 'expiring' && (
            <p className="text-xs text-amber-700">Expires {formatWhen(data.expires_at)} — rotate to renew.</p>
          )}
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-gray-600">
            <dt className="font-medium text-gray-700">Scopes</dt>
            <dd>{(data.scopes ?? []).map((s) => SCOPE_COPY[s].label).join(', ')}</dd>
            <dt className="font-medium text-gray-700">Created</dt>
            <dd>{formatWhen(data.created_at)}</dd>
            <dt className="font-medium text-gray-700">Expires</dt>
            <dd>{data.expires_at ? formatWhen(data.expires_at) : 'never'}</dd>
            <dt className="font-medium text-gray-700">Last used</dt>
            <dd>
              {formatWhen(data.last_used_at)}
              {data.last_used_ip ? ` from ${data.last_used_ip}` : ''}
            </dd>
          </dl>
          <p className="text-xs text-gray-500">
            The full URL was shown once when created. Lost it? Rotate to get a new one; the old URL stops working
            immediately.
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => setMode('rotate')} disabled={busy}>
              Rotate
            </Button>
            <Button size="sm" variant="danger" onClick={() => setConfirming('revoke')} disabled={busy}>
              Revoke
            </Button>
          </div>
        </div>
      )}

      {data.enabled && live && mode === 'rotate' && (
        <McpScopeForm
          isAdmin={isAdmin}
          initial={data.scopes ?? ['read']}
          busy={busy}
          submitLabel="Rotate"
          onSubmit={(scopes) => {
            setPendingScopes(scopes)
            setConfirming('rotate')
          }}
          onCancel={() => setMode('idle')}
        />
      )}

      <ConfirmDialog
        isOpen={confirming === 'rotate'}
        title="Rotate connector URL?"
        message="The current URL stops working immediately. You will need to paste the new one into claude.ai."
        confirmLabel="Rotate"
        busy={busy}
        onConfirm={() => pendingScopes && doMint(pendingScopes)}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        isOpen={confirming === 'revoke'}
        title="Revoke connector?"
        message="Claude will no longer be able to reach this instance with the current URL. You can create a new one later."
        confirmLabel="Revoke"
        confirmVariant="danger"
        busy={busy}
        onConfirm={doRevoke}
        onCancel={() => setConfirming(null)}
      />

      {freshUrl && <OneTimeUrlModal url={freshUrl} onClose={() => setFreshUrl(null)} />}
    </div>
  )
}

function AddressBlock({ address }: { address: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
    } catch {
      // Clipboard unavailable: the address is visible for manual selection.
    }
  }
  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <code
          data-testid="mcp-address"
          className="flex-1 bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-xs text-gray-800 overflow-x-auto whitespace-nowrap select-all"
        >
          {address}
        </code>
        <Button size="sm" variant="secondary" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <p className="text-xs text-gray-500">
        Paste this into Claude → Settings → Connectors → “Add custom connector”, then sign in and approve. It
        works in Claude on the web, desktop and phone, and in other apps that support MCP.
      </p>
    </div>
  )
}

function ConnectedApps() {
  const { showToast } = useToast()
  const { data, isLoading } = useOAuthGrants()
  const disconnect = useDisconnectOAuthGrant()
  const [confirming, setConfirming] = useState<OAuthGrant | null>(null)
  const grants = data?.grants ?? []

  async function doDisconnect(grant: OAuthGrant) {
    try {
      await disconnect.mutateAsync(grant.id)
      setConfirming(null)
      showToast('App disconnected', 'success')
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to disconnect', 'error')
    }
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-gray-900">Connected apps</h3>
      {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
      {!isLoading && grants.length === 0 && <p className="text-sm text-gray-500">No apps connected yet.</p>}
      <ul className="divide-y divide-gray-100">
        {grants.map((grant) => (
          <li key={grant.id} className="py-2 flex items-start gap-3 text-sm">
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-900 truncate">{grant.client_name ?? grant.redirect_host}</p>
              <p className="text-xs text-gray-500">
                {grant.redirect_host} · {grant.scopes.map((s) => SCOPE_COPY[s]?.label ?? s).join(', ')} · connected{' '}
                {formatWhen(grant.created_at)} · last used {formatWhen(grant.last_used_at)}
              </p>
            </div>
            <Button size="sm" variant="secondary" onClick={() => setConfirming(grant)} disabled={disconnect.isPending}>
              Disconnect
            </Button>
          </li>
        ))}
      </ul>
      <ConfirmDialog
        isOpen={confirming !== null}
        title={`Disconnect ${confirming?.client_name ?? confirming?.redirect_host ?? 'this app'}?`}
        message="It stops working on its next request. You can connect it again later."
        confirmLabel="Disconnect"
        confirmVariant="danger"
        busy={disconnect.isPending}
        onConfirm={() => confirming && doDisconnect(confirming)}
        onCancel={() => setConfirming(null)}
      />
    </div>
  )
}

export default function McpConnectorSettings() {
  const { data, isLoading, error } = useMcpToken()
  const oauth = !!data?.oauth_enabled

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">{oauth ? 'Claude and other AI apps' : 'Claude connector (MCP)'}</h2>
        <p className="text-sm text-gray-600 mt-1">
          {oauth
            ? 'Let Claude and other AI apps read your podcasts: summaries, transcripts, quotes and search. Each app asks you to sign in and approve what it may do.'
            : 'Let Claude (mobile, web, desktop) reach your podcasts through a personal connector URL. The URL acts as your login for this instance, narrowed to the scopes you choose, so treat it like a password.'}
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error instanceof Error ? error.message : 'Failed to load'}</p>}
      {isLoading && <p className="text-sm text-gray-500">Loading…</p>}

      {data && oauth && data.address && <AddressBlock address={data.address} />}
      {data && oauth && <ConnectedApps />}
      {data &&
        (oauth ? (
          <details className="text-sm">
            <summary className="cursor-pointer text-gray-700">Advanced: personal connector URL</summary>
            <p className="text-xs text-gray-500 mt-2 mb-3">
              For apps that cannot sign in. The URL acts as your login, narrowed to the scopes you choose, so treat
              it like a password.
            </p>
            <PersonalUrlSection data={data} />
          </details>
        ) : (
          <PersonalUrlSection data={data} />
        ))}
    </div>
  )
}
