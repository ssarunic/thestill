import { useEffect, useState } from 'react'
import { getMcpStatus } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import type { McpStatus } from '../api/types'

// Remote MCP connector card (spec #71 Phase 1). Shows the capability URL
// admins paste into claude.ai → Settings → Connectors → "Add custom
// connector", so Claude (mobile/web/desktop) can talk to this instance.
//
// The URL *is* the credential (operator-equivalent access), so it renders
// masked by default — a screen-share shouldn't leak it — with explicit
// reveal and copy actions. Non-admins never see the card: the endpoint is
// admin-gated server-side and the card also gates on `isAdmin` locally.

export default function McpConnectorSettings() {
  const { isAdmin } = useAuth()
  const [status, setStatus] = useState<McpStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [copiedAt, setCopiedAt] = useState<number | null>(null)

  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    getMcpStatus()
      .then((res) => {
        if (!cancelled) setStatus(res.mcp)
      })
      .catch((err) => {
        // 403 = not an admin after all (stale flag); anything else is a
        // real failure worth surfacing.
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load MCP status')
        }
      })
    return () => {
      cancelled = true
    }
  }, [isAdmin])

  if (!isAdmin) return null

  async function handleCopy() {
    if (!status?.url) return
    try {
      await navigator.clipboard.writeText(status.url)
      setCopiedAt(Date.now())
    } catch {
      // Clipboard can be unavailable (http, permissions); reveal instead
      // so the user can select it manually.
      setRevealed(true)
    }
  }

  const maskedUrl = status?.url ? status.url.replace(/\/mcp\/.+$/, '/mcp/••••••••') : ''

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Claude connector (MCP)</h2>
        <p className="text-sm text-gray-600 mt-1">
          Connect Claude (mobile, web, desktop) directly to this instance. On claude.ai go
          to Settings → Connectors → “Add custom connector” and paste the URL below.
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      {!error && status && !status.enabled && (
        <div className="text-sm text-gray-600 space-y-2">
          <p>
            Remote MCP is disabled. To enable it, set these in your server environment and
            restart:
          </p>
          <pre className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs overflow-x-auto">
            {'MCP_HTTP_ENABLED=true\nMCP_HTTP_SECRET=  # openssl rand -hex 32'}
          </pre>
        </div>
      )}

      {!error && status && status.enabled && status.url && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <code
              data-testid="mcp-url"
              className="flex-1 bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-xs text-gray-800 overflow-x-auto whitespace-nowrap"
            >
              {revealed ? status.url : maskedUrl}
            </code>
            <button
              type="button"
              onClick={() => setRevealed((r) => !r)}
              className="px-3 py-2 text-xs font-medium text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
            >
              {revealed ? 'Hide' : 'Reveal'}
            </button>
            <button
              type="button"
              onClick={handleCopy}
              className="px-3 py-2 text-xs font-medium text-white bg-primary-900 rounded-md hover:bg-primary-800"
            >
              {copiedAt ? 'Copied' : 'Copy'}
            </button>
          </div>
          <p className="text-xs text-gray-500">
            Treat this URL like a password: anyone who has it can use every MCP tool on this
            instance. It only travels safely over HTTPS. Rotate it by changing{' '}
            <code>MCP_HTTP_SECRET</code> and restarting the server.
          </p>
        </div>
      )}
    </div>
  )
}
