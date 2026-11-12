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

/**
 * Parses a `?t=<seconds>` (or `#t=`) deep link once per key change. `key` is
 * typically the episode id, and `ready` waits for real data before firing.
 *
 * A moment the visitor clicked inside the app goes to `onSeek`, which plays
 * it. A link opened cold goes to `onCue` instead: nothing plays until the
 * visitor asks, so the page offers the moment rather than attempting an
 * autoplay the browser would block.
 */
export function useDeepLinkSeek(
  key: string | number | null,
  onSeek: (seconds: number) => void,
  ready: boolean,
  onCue?: (seconds: number) => void,
) {
  const firedForKey = useRef<string | number | null>(null)
  useEffect(() => {
    if (!ready || key == null) return
    if (firedForKey.current === key) return
    if (typeof window === 'undefined') return
    firedForKey.current = key
    const seconds = deepLinkSeconds(window.location.search, window.location.hash)
    if (seconds === null) return
    if (hasUserGesture()) onSeek(seconds)
    else onCue?.(seconds)
  }, [key, onSeek, onCue, ready])
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
