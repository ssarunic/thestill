import { useEffect, useRef } from 'react'

/**
 * Parses a `?t=<seconds>` query param once and fires the supplied seek
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
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('t')
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
