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

/**
 * Parses a `?t=<seconds>` (or `#t=`) deep link once and fires the supplied seek
 * callback exactly one time per key change. `key` is typically the
 * episode id + the count of loaded segments, so the effect waits for
 * real data before firing.
 */
export function useDeepLinkSeek(
  key: string | number | null,
  onSeek: (seconds: number) => void,
  ready: boolean,
) {
  const firedForKey = useRef<string | number | null>(null)
  useEffect(() => {
    if (!ready || key == null) return
    if (firedForKey.current === key) return
    if (typeof window === 'undefined') return
    const raw = deepLinkParam(window.location.search, window.location.hash, 't')
    if (!raw) {
      firedForKey.current = key
      return
    }
    const seconds = Number(raw)
    if (Number.isFinite(seconds) && seconds >= 0) {
      onSeek(seconds)
    }
    firedForKey.current = key
  }, [key, onSeek, ready])
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
