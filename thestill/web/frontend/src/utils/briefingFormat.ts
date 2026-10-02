// Listener-facing formatting for the briefing page.

import type { BriefingPodcastGroup } from '../api/types'

/**
 * ``From The Rest Is Politics, Hard Fork and 3 more`` — the identity line
 * under the briefing title.
 */
export function describeShows(podcasts: BriefingPodcastGroup[], max = 3): string | null {
  const titles = podcasts.map((podcast) => podcast.title)
  if (titles.length === 0) return null
  if (titles.length === 1) return `From ${titles[0]}`
  if (titles.length <= max) {
    return `From ${titles.slice(0, -1).join(', ')} and ${titles[titles.length - 1]}`
  }
  const shown = titles.slice(0, max - 1)
  return `From ${shown.join(', ')} and ${titles.length - shown.length} more`
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/**
 * Names an edition by when it was cut — ``This morning's briefing``,
 * ``Yesterday's briefing``, ``Monday's briefing``, ``Briefing · Sep 12`` —
 * so an edition that is still the current one a day later never claims to
 * be "today's".
 */
export function editionTitle(iso: string, now: Date = new Date()): string {
  const created = new Date(iso)
  const daysAgo = Math.round((startOfDay(now) - startOfDay(created)) / 86_400_000)
  if (daysAgo <= 0) {
    const hour = created.getHours()
    const part = hour < 12 ? 'This morning' : hour < 17 ? 'This afternoon' : 'This evening'
    return `${part}'s briefing`
  }
  if (daysAgo === 1) return "Yesterday's briefing"
  if (daysAgo < 7) return `${created.toLocaleDateString(undefined, { weekday: 'long' })}'s briefing`
  return `Briefing · ${created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
}

/** ``today 8:00 AM`` / ``tomorrow 8:00 AM`` / ``Mon 8:00 AM`` for a coming slot. */
export function formatUpcomingSlot(iso: string, now: Date = new Date()): string {
  const slot = new Date(iso)
  const time = slot.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const daysAhead = Math.round((startOfDay(slot) - startOfDay(now)) / 86_400_000)
  if (daysAhead <= 0) return `today ${time}`
  if (daysAhead === 1) return `tomorrow ${time}`
  return `${slot.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`
}
