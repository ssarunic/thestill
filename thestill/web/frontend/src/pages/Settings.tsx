import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import BriefingScheduleSettings from '../components/BriefingScheduleSettings'

// Region dropdown options. The data layer ships top-podcast rankings for
// "us", "gb", and the whole EEA (spec #57); the remaining entries are common
// markets listed so the user can still pick something stable that won't get
// re-inferred. Keep this in sync with the FLAG map in utils/regions.ts —
// the two lists must cover the same codes (FM-6). When new region files land,
// just append here. Liechtenstein (li) is intentionally absent: Apple has no
// Liechtenstein storefront chart, so it can only ever be empty.
const REGIONS: Array<{ code: string; label: string }> = [
  // Primary anchors.
  { code: 'us', label: 'United States' },
  { code: 'gb', label: 'United Kingdom' },
  // EEA (EU 27 + Iceland, Norway), alphabetical by country name.
  { code: 'at', label: 'Austria' },
  { code: 'be', label: 'Belgium' },
  { code: 'bg', label: 'Bulgaria' },
  { code: 'hr', label: 'Croatia' },
  { code: 'cy', label: 'Cyprus' },
  { code: 'cz', label: 'Czechia' },
  { code: 'dk', label: 'Denmark' },
  { code: 'ee', label: 'Estonia' },
  { code: 'fi', label: 'Finland' },
  { code: 'fr', label: 'France' },
  { code: 'de', label: 'Germany' },
  { code: 'gr', label: 'Greece' },
  { code: 'hu', label: 'Hungary' },
  { code: 'is', label: 'Iceland' },
  { code: 'ie', label: 'Ireland' },
  { code: 'it', label: 'Italy' },
  { code: 'lv', label: 'Latvia' },
  { code: 'lt', label: 'Lithuania' },
  { code: 'lu', label: 'Luxembourg' },
  { code: 'mt', label: 'Malta' },
  { code: 'nl', label: 'Netherlands' },
  { code: 'no', label: 'Norway' },
  { code: 'pl', label: 'Poland' },
  { code: 'pt', label: 'Portugal' },
  { code: 'ro', label: 'Romania' },
  { code: 'sk', label: 'Slovakia' },
  { code: 'si', label: 'Slovenia' },
  { code: 'es', label: 'Spain' },
  { code: 'se', label: 'Sweden' },
  // Other common markets (no chart data shipped yet).
  { code: 'ca', label: 'Canada' },
  { code: 'au', label: 'Australia' },
  { code: 'jp', label: 'Japan' },
  { code: 'br', label: 'Brazil' },
  { code: 'mx', label: 'Mexico' },
  { code: 'in', label: 'India' },
]

export default function Settings() {
  const { user, updateRegion } = useAuth()
  const [draftRegion, setDraftRegion] = useState<string>(user?.region ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  if (!user) {
    return <div className="text-gray-500">Sign in to manage your settings.</div>
  }

  const inferenceLabel = user.region_locked
    ? 'You picked this manually.'
    : user.region
      ? 'Auto-detected from your IP. Pick one below to lock it in.'
      : "We couldn't auto-detect your region. Pick one below."

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await updateRegion(draftRegion ? draftRegion : null)
      setSavedAt(Date.now())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Settings</h1>
        <p className="text-gray-600">Manage your account preferences.</p>
      </div>

      <BriefingScheduleSettings />

      <form
        onSubmit={handleSubmit}
        className="bg-white border border-gray-200 rounded-lg p-6 space-y-4"
      >
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Region</h2>
          <p className="text-sm text-gray-600 mt-1">
            Drives which charts you see when browsing top podcasts. {inferenceLabel}
          </p>
        </div>

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Country</span>
          <select
            value={draftRegion}
            onChange={(e) => setDraftRegion(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">— Not set —</option>
            {REGIONS.map((r) => (
              <option key={r.code} value={r.code}>
                {r.label} ({r.code.toUpperCase()})
              </option>
            ))}
          </select>
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}
        {savedAt && !error && (
          <p className="text-sm text-green-600">Saved.</p>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={saving || draftRegion === (user.region ?? '')}
            className="px-4 py-2 bg-primary-900 text-white rounded-md text-sm font-medium hover:bg-primary-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          <span className="text-xs text-gray-500">
            Current: {user.region ? user.region.toUpperCase() : 'unset'}
            {user.region_locked ? ' · locked' : ''}
          </span>
        </div>
      </form>
    </div>
  )
}
