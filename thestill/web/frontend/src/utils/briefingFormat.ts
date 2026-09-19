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
