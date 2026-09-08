import { useState } from 'react'
import Button from './Button'
import ConfirmDialog from './ConfirmDialog'
import { useToast } from './Toast'
import { useAuth } from '../contexts/AuthContext'
import { useCreateOrRotateMcpToken, useMcpToken, useRevokeMcpToken } from '../hooks/useApi'
import type { McpTokenInfo, McpTokenScope } from '../api/types'

// Per-user Claude connector card (spec #78 Phase 2).
//
// The connector URL *is* a credential (equivalent to this user's web
// session, narrowed by scopes), so the plaintext is shown exactly once —
// in a modal right after Create/Rotate — and never again: the server
// stores only a hash. There is no Reveal; a user who lost the URL
// rotates. Rotate and Revoke confirm through ConfirmDialog.

const SCOPE_COPY: Record<McpTokenScope, { label: string; hint: string }> = {
  read: { label: 'Read', hint: 'Podcasts, episodes, transcripts, summaries, search. Always on.' },
  follows: { label: 'Follows', hint: 'Add podcasts and unfollow. Adding a feed runs the pipeline, so this is off by default.' },
  pipeline: { label: 'Pipeline', hint: 'Refresh, download, transcribe, summarise. Admins only.' },
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function ScopeForm({
  isAdmin,
  initial,
  onSubmit,
  onCancel,
  busy,
  submitLabel,
}: {
  isAdmin: boolean
  initial: McpTokenScope[]
  onSubmit: (scopes: McpTokenScope[]) => void
  onCancel?: () => void
  busy: boolean
  submitLabel: string
}) {
  const [follows, setFollows] = useState(initial.includes('follows'))
  const [pipeline, setPipeline] = useState(initial.includes('pipeline'))
  const offered: McpTokenScope[] = isAdmin ? ['read', 'follows', 'pipeline'] : ['read', 'follows']

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        const scopes: McpTokenScope[] = ['read']
        if (follows) scopes.push('follows')
        if (pipeline && isAdmin) scopes.push('pipeline')
        onSubmit(scopes)
      }}
    >
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-gray-900">What this connector may do</legend>
        {offered.map((scope) => {
          const checked = scope === 'read' ? true : scope === 'follows' ? follows : pipeline
          const setter = scope === 'follows' ? setFollows : setPipeline
          return (
            <label key={scope} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={checked}
                disabled={scope === 'read' || busy}
                onChange={(e) => setter(e.target.checked)}
                aria-label={SCOPE_COPY[scope].label}
              />
              <span>
                <span className="font-medium text-gray-900">{SCOPE_COPY[scope].label}</span>
                <span className="block text-xs text-gray-500">{SCOPE_COPY[scope].hint}</span>
              </span>
            </label>
          )
        })}
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" size="sm" isLoading={busy}>
          {submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  )
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

export default function McpConnectorSettings() {
  const { isAdmin } = useAuth()
  const { showToast } = useToast()
  const { data, isLoading, error } = useMcpToken()
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
      showToast(data?.state === 'active' || data?.state === 'expiring' ? 'Connector rotated' : 'Connector created', 'success')
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

  const live = data?.state === 'active' || data?.state === 'expiring'

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Claude connector (MCP)</h2>
        <p className="text-sm text-gray-600 mt-1">
          Let Claude (mobile, web, desktop) reach your podcasts through a personal connector URL. The URL acts
          as your login for this instance, narrowed to the scopes you choose, so treat it like a password.
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error instanceof Error ? error.message : 'Failed to load'}</p>}
      {isLoading && <p className="text-sm text-gray-500">Loading…</p>}

      {data && !data.enabled && (
        <div className="text-sm text-gray-600 space-y-2">
          <p>Remote MCP is disabled on this server. An operator turned it off with:</p>
          <pre className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs overflow-x-auto">
            {'MCP_HTTP_ENABLED=false'}
          </pre>
        </div>
      )}

      {data && data.enabled && !live && mode === 'idle' && (
        <div className="space-y-3 text-sm text-gray-600">
          {data.state === 'revoked' && <p>Revoked on {formatWhen(data.revoked_at)}.</p>}
          {data.state === 'expired' && <p>Expired on {formatWhen(data.expires_at)}.</p>}
          {data.state === 'none' && <p>No connector yet.</p>}
          <Button size="sm" onClick={() => setMode('create')}>
            Create connector URL
          </Button>
        </div>
      )}

      {data && data.enabled && mode === 'create' && (
        <ScopeForm
          isAdmin={isAdmin}
          initial={['read']}
          busy={busy}
          submitLabel="Create"
          onSubmit={doMint}
          onCancel={() => setMode('idle')}
        />
      )}

      {data && data.enabled && live && mode === 'idle' && (
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

      {data && data.enabled && live && mode === 'rotate' && (
        <ScopeForm
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
