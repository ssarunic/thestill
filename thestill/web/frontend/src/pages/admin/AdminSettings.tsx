import { useState } from 'react'
import type { AdminSetting } from '../../api/types'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import { useAdminToast } from '../../hooks/useAdminToast'
import { useAdminSettings, usePutAdminSetting, useResetAdminSetting, useSendTestAlert } from '../../hooks/useAdmin'

const SWITCHES: { key: string; label: string }[] = [
  { key: 'new_feeds_enabled', label: 'New feeds' },
  { key: 'imports_enabled', label: 'Imports' },
  { key: 'translations_enabled', label: 'Summary translations' },
  { key: 'narration_enabled', label: 'Briefing narration' },
]

function SourceNote({ setting, onReset }: { setting: AdminSetting; onReset: () => void }) {
  return (
    <span className="text-xs text-muted">
      {setting.source === 'runtime' ? (
        <>
          set at runtime ·{' '}
          <button type="button" className="underline" onClick={onReset}>
            reset to {JSON.stringify(setting.default)}
          </button>
        </>
      ) : (
        'env default'
      )}
    </span>
  )
}

/** Spec #104 §4.3 — the signup gate, the runtime switches and the alerts. */
export default function AdminSettings() {
  const { data, isLoading, error } = useAdminSettings()
  const put = usePutAdminSetting()
  const reset = useResetAdminSetting()
  const testAlert = useSendTestAlert()
  const toast = useAdminToast()
  const [allowlistDraft, setAllowlistDraft] = useState<string | null>(null)
  const [alertsDraft, setAlertsDraft] = useState<string | null>(null)
  const [confirmReadOnly, setConfirmReadOnly] = useState(false)

  if (error) return <p className="text-red-600 text-sm">{error.message}</p>
  if (isLoading || !data) return <div className="animate-pulse h-64 bg-surface border border-hairline rounded-lg" />

  const byKey = Object.fromEntries(data.settings.map((s) => [s.key, s]))
  const save = (key: string, value: unknown, message = 'Saved') =>
    put.mutate({ key, value }, { onSuccess: () => toast.ok(message), onError: (e) => toast.fail(e) })
  const resetKey = (key: string) =>
    reset.mutate(key, { onSuccess: () => toast.ok('Reset to the env default'), onError: (e) => toast.fail(e) })

  const signupMode = byKey.signup_mode
  const allowlist = byKey.signup_allowlist
  const readOnly = byKey.read_only
  const alerts = byKey.alerts
  const allowlistText = allowlistDraft ?? (allowlist.value as string[]).join('\n')
  const alertsText = alertsDraft ?? JSON.stringify(alerts.value, null, 2)

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-ink">Admin settings</h1>
        <p className="text-muted mt-1">Runtime switches. Every server process picks a change up within 15 seconds.</p>
      </div>

      <section className="bg-surface border border-hairline rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-ink">Signups</h2>
        <fieldset className="flex flex-wrap gap-4 text-sm">
          <legend className="sr-only">Signup mode</legend>
          {(['open', 'allowlist', 'closed'] as const).map((mode) => (
            <label key={mode} className="flex items-center gap-2">
              <input
                type="radio"
                name="signup_mode"
                checked={signupMode.value === mode}
                onChange={() => save('signup_mode', mode, `Signups: ${mode}`)}
              />
              {mode}
            </label>
          ))}
        </fieldset>
        <SourceNote setting={signupMode} onReset={() => resetKey('signup_mode')} />
        <label className="block text-sm">
          <span className="block text-ink">Allowlist — one email or @domain per line (used in allowlist mode)</span>
          <textarea
            rows={4}
            value={allowlistText}
            onChange={(e) => setAllowlistDraft(e.target.value)}
            className="mt-1 w-full rounded-md border border-hairline bg-surface p-2 text-ink font-mono text-xs"
          />
        </label>
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            disabled={allowlistDraft === null}
            isLoading={put.isPending}
            onClick={() =>
              put.mutate(
                { key: 'signup_allowlist', value: allowlistText.split('\n').map((l) => l.trim()).filter(Boolean) },
                {
                  onSuccess: () => { setAllowlistDraft(null); toast.ok('Allowlist saved') },
                  onError: (e) => toast.fail(e),
                },
              )
            }
          >
            Save allowlist
          </Button>
          <SourceNote setting={allowlist} onReset={() => resetKey('signup_allowlist')} />
        </div>
      </section>

      <section className="bg-surface border border-hairline rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-ink">Costly features</h2>
        {SWITCHES.map(({ key, label }) => {
          const setting = byKey[key]
          return (
            <div key={key} className="flex flex-wrap items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={setting.value === true} onChange={(e) => save(key, e.target.checked)} />
                {label}
              </label>
              <SourceNote setting={setting} onReset={() => resetKey(key)} />
            </div>
          )
        })}
        <div className="border-t border-hairline pt-3 flex flex-wrap items-center justify-between gap-2">
          <label className="flex items-center gap-2 text-sm font-medium text-red-700">
            <input
              type="checkbox"
              checked={readOnly.value === true}
              onChange={(e) => (e.target.checked ? setConfirmReadOnly(true) : save('read_only', false, 'Read-only mode off'))}
            />
            Read-only mode (panic)
          </label>
          <SourceNote setting={readOnly} onReset={() => resetKey('read_only')} />
        </div>
      </section>

      <section className="bg-surface border border-hairline rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-ink">Alerts</h2>
        <p className="text-sm text-muted">
          Channels: {data.alert_channels.join(', ')}.{' '}
          {data.alert_channels.length === 1 && 'Set ADMIN_ALERT_EMAIL and EMAIL_PROVIDER, or ALERT_WEBHOOK_URL, for more.'}
        </p>
        <Button
          size="sm"
          variant="secondary"
          isLoading={testAlert.isPending}
          onClick={() =>
            testAlert.mutate(undefined, {
              onSuccess: ({ results }) => {
                const failed = Object.entries(results).filter(([, ok]) => !ok).map(([name]) => name)
                if (failed.length) toast.fail(new Error(`Failed: ${failed.join(', ')}`))
                else toast.ok('Test alert sent')
              },
              onError: (e) => toast.fail(e),
            })
          }
        >
          Send test alert
        </Button>
        <label className="block text-sm">
          <span className="block text-ink">
            Threshold overrides (JSON) — quota_pressure, rate_limit_pressure, signup_spike, feed_spike, queue_backlog,
            spend_breaker_minutes, spend_breaker_action
          </span>
          <textarea
            rows={5}
            value={alertsText}
            onChange={(e) => setAlertsDraft(e.target.value)}
            className="mt-1 w-full rounded-md border border-hairline bg-surface p-2 text-ink font-mono text-xs"
          />
        </label>
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            disabled={alertsDraft === null}
            onClick={() => {
              let value: unknown
              try {
                value = JSON.parse(alertsText)
              } catch {
                toast.fail(new Error('Not valid JSON'))
                return
              }
              put.mutate(
                { key: 'alerts', value },
                {
                  onSuccess: () => { setAlertsDraft(null); toast.ok('Alert thresholds saved') },
                  onError: (e) => toast.fail(e),
                },
              )
            }}
          >
            Save thresholds
          </Button>
          <SourceNote setting={alerts} onReset={() => resetKey('alerts')} />
        </div>
      </section>

      <ConfirmDialog
        isOpen={confirmReadOnly}
        title="Turn on read-only mode?"
        message="Every non-admin write and every MCP mutating tool is refused, and new feeds, imports, translations and narration all pause. Reading still works."
        confirmLabel="Turn on read-only"
        confirmVariant="danger"
        busy={put.isPending}
        onCancel={() => setConfirmReadOnly(false)}
        onConfirm={() =>
          put.mutate(
            { key: 'read_only', value: true },
            {
              onSuccess: () => { setConfirmReadOnly(false); toast.ok('Read-only mode on') },
              onError: (e) => toast.fail(e),
            },
          )
        }
      />
    </div>
  )
}
