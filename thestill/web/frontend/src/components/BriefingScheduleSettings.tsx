import { useEffect, useState } from 'react'
import { getBriefingSchedule, putBriefingSchedule } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import type { BriefingFrequency } from '../api/types'

// Briefing schedule editor (spec #50): when (hour in timezone) and how
// often (daily / weekly + weekday) the morning briefing is generated.
// Never configured (API 404) renders as disabled defaults seeded with the
// browser's timezone; saving creates the row.
// Spec #51: an "email it to me" checkbox, shown only when the server has
// an email provider configured (auth-status capability flag).

const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
]

const HOURS = Array.from({ length: 24 }, (_, h) => h)

const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone

// Intl.supportedValuesOf is ES2022; the tsconfig lib predates it, so
// feature-detect through a typed view instead of widening the lib.
const intl = Intl as typeof Intl & {
  supportedValuesOf?: (key: 'timeZone') => string[]
}

const TIMEZONES: string[] = intl.supportedValuesOf
  ? intl.supportedValuesOf('timeZone')
  : [browserTimezone]

interface Draft {
  enabled: boolean
  frequency: BriefingFrequency
  hourLocal: number
  weekday: number
  timezone: string
  emailEnabled: boolean
}

const DEFAULT_DRAFT: Draft = {
  enabled: false,
  frequency: 'daily',
  hourLocal: 8,
  weekday: 0,
  timezone: browserTimezone,
  emailEnabled: false,
}

export default function BriefingScheduleSettings() {
  const { emailDeliveryAvailable } = useAuth()
  const [draft, setDraft] = useState<Draft>(DEFAULT_DRAFT)
  const [saved, setSaved] = useState<Draft | null>(null)
  const [nextRunAt, setNextRunAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getBriefingSchedule()
      .then((schedule) => {
        if (cancelled) return
        const loaded: Draft = {
          enabled: schedule.enabled,
          frequency: schedule.frequency,
          hourLocal: schedule.hour_local,
          weekday: schedule.weekday ?? 0,
          timezone: schedule.timezone,
          emailEnabled: schedule.email_enabled ?? false,
        }
        setDraft(loaded)
        setSaved(loaded)
        setNextRunAt(schedule.next_run_at)
      })
      .catch(() => {
        // 404 = never configured; keep the browser-timezone defaults.
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const dirty =
    saved === null ||
    draft.enabled !== saved.enabled ||
    draft.frequency !== saved.frequency ||
    draft.hourLocal !== saved.hourLocal ||
    (draft.frequency === 'weekly' && draft.weekday !== saved.weekday) ||
    draft.timezone !== saved.timezone ||
    draft.emailEnabled !== saved.emailEnabled

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const schedule = await putBriefingSchedule({
        enabled: draft.enabled,
        frequency: draft.frequency,
        hour_local: draft.hourLocal,
        weekday: draft.frequency === 'weekly' ? draft.weekday : null,
        timezone: draft.timezone,
        email_enabled: emailDeliveryAvailable ? draft.emailEnabled : false,
      })
      setSaved({ ...draft })
      setNextRunAt(schedule.next_run_at)
      setSavedAt(Date.now())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-6 text-sm text-gray-500">
        Loading briefing schedule…
      </div>
    )
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white border border-gray-200 rounded-lg p-6 space-y-4"
    >
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Briefing schedule</h2>
        <p className="text-sm text-gray-600 mt-1">
          Generate your briefing automatically from everything that landed in
          your inbox since the previous one — ready before you open the app.
        </p>
      </div>

      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          className="rounded border-gray-300 text-primary-900 focus:ring-primary-500"
        />
        <span className="text-sm font-medium text-gray-700">
          Generate my briefing on a schedule
        </span>
      </label>

      <div className={`grid gap-4 sm:grid-cols-2 ${draft.enabled ? '' : 'opacity-50'}`}>
        <label className="block">
          <span className="text-sm font-medium text-gray-700">Frequency</span>
          <select
            value={draft.frequency}
            disabled={!draft.enabled}
            onChange={(e) =>
              setDraft({ ...draft, frequency: e.target.value as BriefingFrequency })
            }
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="daily">Daily (each morning)</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>

        {draft.frequency === 'weekly' && (
          <label className="block">
            <span className="text-sm font-medium text-gray-700">Day of week</span>
            <select
              value={draft.weekday}
              disabled={!draft.enabled}
              onChange={(e) => setDraft({ ...draft, weekday: Number(e.target.value) })}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {WEEKDAYS.map((day, index) => (
                <option key={day} value={index}>
                  {day}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Time</span>
          <select
            value={draft.hourLocal}
            disabled={!draft.enabled}
            onChange={(e) => setDraft({ ...draft, hourLocal: Number(e.target.value) })}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            {HOURS.map((hour) => (
              <option key={hour} value={hour}>
                {String(hour).padStart(2, '0')}:00
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Timezone</span>
          <select
            value={draft.timezone}
            disabled={!draft.enabled}
            onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            {!TIMEZONES.includes(draft.timezone) && (
              <option value={draft.timezone}>{draft.timezone}</option>
            )}
            {TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
        </label>
      </div>

      {emailDeliveryAvailable && (
        <label className={`flex items-center gap-2 ${draft.enabled ? '' : 'opacity-50'}`}>
          <input
            type="checkbox"
            checked={draft.emailEnabled}
            disabled={!draft.enabled}
            onChange={(e) => setDraft({ ...draft, emailEnabled: e.target.checked })}
            className="rounded border-gray-300 text-primary-900 focus:ring-primary-500"
          />
          <span className="text-sm font-medium text-gray-700">
            Email each briefing to me when it&apos;s ready
          </span>
        </label>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
      {savedAt && !error && <p className="text-sm text-green-600">Saved.</p>}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={saving || !dirty}
          className="px-4 py-2 bg-primary-900 text-white rounded-md text-sm font-medium hover:bg-primary-800 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <span className="text-xs text-gray-500">
          {nextRunAt
            ? `Next briefing: ${new Date(nextRunAt).toLocaleString(undefined, {
                weekday: 'short',
                hour: '2-digit',
                minute: '2-digit',
                day: 'numeric',
                month: 'short',
              })}`
            : 'Scheduling off — briefings generate when you open your inbox.'}
        </span>
      </div>
    </form>
  )
}
