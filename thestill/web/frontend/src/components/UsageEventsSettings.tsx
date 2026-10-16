import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

/**
 * Spec #95 — the per-user switch for usage events (PostHog). Shown only in
 * multi-user mode when usage events are configured. The server enforces it:
 * off means the app gets no analytics key and the server drops this
 * reader's events.
 */
export default function UsageEventsSettings() {
  const { user, isMultiUser, usageAnalyticsAvailable, updateUsageAnalytics } = useAuth()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!user || !isMultiUser || !usageAnalyticsAvailable) return null
  const enabled = user.usage_analytics_enabled !== false

  async function toggle() {
    setError(null)
    setSaving(true)
    try {
      await updateUsageAnalytics(!enabled)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="bg-white border border-gray-200 rounded-lg p-6 space-y-3" aria-labelledby="usage-events-heading">
      <h2 id="usage-events-heading" className="text-lg font-semibold text-gray-900">
        Usage events
      </h2>
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4"
          checked={enabled}
          disabled={saving}
          onChange={toggle}
        />
        <span className="text-sm text-gray-700">
          Help improve thestill by recording which features you use: pages by type, searches (never what you
          searched for), shares and connector calls. No page text, no addresses.
        </span>
      </label>
      <p className="text-xs text-gray-500">
        Your follows, reads and briefings still count towards our overall numbers either way, because the
        service keeps them to work. Your browser&apos;s Global Privacy Control setting is honoured too.
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </section>
  )
}
