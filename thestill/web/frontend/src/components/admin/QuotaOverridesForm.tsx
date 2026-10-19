import { useState } from 'react'
import { useSetQuotaOverrides } from '../../hooks/useAdmin'
import { useAdminToast } from '../../hooks/useAdminToast'
import Button from '../Button'

// Readable names; a quota the server adds later still shows, under its key.
const LABELS: Record<string, string> = {
  feeds_introduced_per_day: 'Feeds brought in / day',
  follows_total: 'Follows (total)',
  follows_per_day: 'Follows / day',
  imports_per_day: 'Imports / day',
  translations_per_day: 'Translations / day',
  narrations_per_day: 'Narrations / day',
}

export interface QuotaOverridesFormProps {
  userId: string
  /** Every quota the server knows, with this user's effective limit. */
  limits: Record<string, number>
  overrides: Record<string, number>
}

/** Spec #104 §4.2 — per-user quota overrides (empty = the default). */
export default function QuotaOverridesForm({ userId, limits, overrides }: QuotaOverridesFormProps) {
  const toast = useAdminToast()
  const save = useSetQuotaOverrides()
  const [draft, setDraft] = useState<Record<string, string> | null>(null)
  const names = Object.keys(limits)
  const values = draft ?? Object.fromEntries(names.map((name) => [name, overrides[name]?.toString() ?? '']))

  const submit = () => {
    const next: Record<string, number> = {}
    for (const [name, raw] of Object.entries(values)) {
      if (raw.trim() !== '') next[name] = Number(raw)
    }
    if (Object.values(next).some((n) => !Number.isInteger(n) || n < 0)) {
      toast.fail(new Error('Limits are whole numbers, 0 for unlimited.'))
      return
    }
    save.mutate(
      { id: userId, overrides: next },
      {
        onSuccess: () => {
          setDraft(null)
          toast.ok('Quota overrides saved')
        },
        onError: toast.fail,
      },
    )
  }

  return (
    <section className="bg-surface border border-hairline rounded-lg p-4">
      <h2 className="font-semibold text-ink mb-1">Quota overrides</h2>
      <p className="text-sm text-muted mb-3">Empty uses the default shown; 0 means unlimited.</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl">
        {names.map((name) => (
          <label key={name} className="text-sm">
            <span className="block text-ink">{LABELS[name] ?? name}</span>
            <input
              inputMode="numeric"
              value={values[name] ?? ''}
              placeholder={`default ${limits[name]}`}
              onChange={(e) => setDraft({ ...values, [name]: e.target.value })}
              className="mt-1 w-full rounded-md border border-hairline bg-surface px-2 py-1 text-ink"
            />
          </label>
        ))}
      </div>
      <Button size="sm" className="mt-3" onClick={submit} isLoading={save.isPending} disabled={!draft}>
        Save overrides
      </Button>
    </section>
  )
}
