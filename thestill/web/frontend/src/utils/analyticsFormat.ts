// Spec #95 — formatting for the Layer A analytics page.
import type { AnalyticsRange } from '../api/types'

export const RANGES: readonly { key: AnalyticsRange; label: string }[] = [
  { key: '7d', label: '7 days' },
  { key: '28d', label: '28 days' },
  { key: '90d', label: '90 days' },
]
export const DEFAULT_RANGE: AnalyticsRange = '28d'

/** Groups smaller than this are greyed out, not hidden (spec #95 D1). */
export const SMALL_GROUP = 5

const SOURCE_LABELS: Record<string, string> = {
  all: 'All',
  landing: 'Landing',
  top: 'Top charts',
  podcast: 'Show page',
  episode: 'Episode page',
  entity: 'Person / topic',
  share: 'Share',
  direct: 'Direct',
  before_tracking: 'Before tracking',
}

export function parseRange(raw: string | null): AnalyticsRange {
  return RANGES.find((r) => r.key === raw)?.key ?? DEFAULT_RANGE
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

/** ``0.4567`` → ``"45.7%"``; null → ``"—"``. */
export function formatPct(pct: number | null): string {
  return pct == null ? '—' : `${(pct * 100).toFixed(1)}%`
}

/** Median delay: minutes under an hour, hours under two days, else days. */
export function formatHours(hours: number | null): string {
  if (hours == null) return '—'
  if (hours < 1) return `${Math.round(hours * 60)} min`
  if (hours < 48) return `${hours.toFixed(1)} h`
  return `${(hours / 24).toFixed(1)} d`
}

export function formatRatio(value: number | null, digits = 1): string {
  return value == null ? '—' : value.toFixed(digits)
}

export function isSmallGroup(n: number): boolean {
  return n < SMALL_GROUP
}

/** A Monday ``2026-10-12`` → ``Oct 12`` (UTC, so it never shifts a day). */
export function formatWeek(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
