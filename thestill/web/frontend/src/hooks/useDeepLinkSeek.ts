import { useEffect, useRef } from 'react'

/**
 * Reads a deep-link parameter (`t`, `about`) from the query string, falling
 * back to the fragment. Public pages link with the fragment (`#t=238`) so a
 * crawler sees one URL per episode; Google indexed every `?t=` variant as its
 * own page despite the canonical tag. The query form keeps working for links
 * shared before and for in-app links.
 */
export function deepLinkParam(search: string, hash: string, name: string): string | null {
  return new URLSearchParams(search).get(name) ?? new URLSearchParams(hash.replace(/^#/, '')).get(name)
}

/** The `t` deep-link parameter as whole-or-fractional seconds, or null when
 * it is absent or not a time. */
export function deepLinkSeconds(search: string, hash: string): number | null {
  const raw = deepLinkParam(search, hash, 't')
  if (!raw) return null
  const seconds = Number(raw)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null
}

/**
 * Whether the visitor has clicked, tapped or pressed a key in this document.
 * A link followed inside the app has; a shared link opened cold has not, and
 * a browser refuses to start audio for it (Safari most strictly).
 */
export function hasUserGesture(): boolean {
  return typeof navigator !== 'undefined' && navigator.userActivation?.hasBeenActive === true
}

// What became of the moment each history entry linked to: the seconds still
// offered by "Play from", or null once dealt with (played, or no moment).
// Module-level so a return to the entry (Back/Forward, a remount across
// page/overlay hosts) neither seeks again — it would restart or rewind the
// audio — nor forgets an offer the visitor has not taken up.
const entries = new Map<string, number | null>()

const entryId = (key: string | number, entryKey: string) => `${key}|${entryKey}`

/** The visitor took up (or no longer needs) the "Play from" offer. */
export function settleDeepLinkCue(key: string | number | null, entryKey = 'default'): void {
  if (key != null && entries.has(entryId(key, entryKey))) entries.set(entryId(key, entryKey), null)
}

/** Test seam. */
export function __resetDeepLinkSeekForTests(): void {
  entries.clear()
}

/**
 * Parses a `?t=<seconds>` (or `#t=`) deep link once per history entry. `key`
 * is the episode id, `entryKey` the router's `location.key`, and `ready`
 * waits for real data before firing.
 *
 * A moment the visitor clicked inside the app goes to `onSeek`, which plays
 * it. A link opened cold does not: a browser blocks audio nobody asked for.
 * Either way `onCue` gets the moment, so the page can offer "Play from" —
 * the only way in for a cold link, and the fallback when the browser refuses
 * the in-app play as well (Safari, once the click is no longer fresh).
 */
export function useDeepLinkSeek(
  key: string | number | null,
  onSeek: (seconds: number) => void,
  ready: boolean,
  onCue?: (seconds: number) => void,
  entryKey = 'default',
) {
  const firedFor = useRef<string | null>(null)
  useEffect(() => {
    if (!ready || key == null) return
    if (typeof window === 'undefined') return
    const id = entryId(key, entryKey)
    if (firedFor.current === id) return
    firedFor.current = id
    if (entries.has(id)) {
      const pending = entries.get(id)
      if (pending != null) onCue?.(pending)
      return
    }
    const seconds = deepLinkSeconds(window.location.search, window.location.hash)
    entries.set(id, seconds)
    if (seconds === null) return
    if (hasUserGesture()) onSeek(seconds)
    onCue?.(seconds)
  }, [key, entryKey, onSeek, onCue, ready])
}

export function buildTimestampDeepLink(seconds: number): string {
  if (typeof window === 'undefined') return `?t=${Math.floor(seconds)}`
  const url = new URL(window.location.href)
  url.searchParams.set('t', String(Math.floor(seconds)))
  return url.toString()
}

/**
 * In-app path to an episode's reader at a moment: `?t=` is whole seconds,
 * which `useDeepLinkSeek` reads back. Every link that lands a reader on a
 * timestamp (entity page rows, ⌘K results, the peek's "Also mentioned on")
 * goes through here so the contract lives in one place.
 */
export function episodeTimestampPath(podcastSlug: string, episodeSlug: string, seconds: number): string {
  return `/podcasts/${podcastSlug}/episodes/${episodeSlug}?t=${Math.floor(seconds)}`
}
