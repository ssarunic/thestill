import { useCallback, useSyncExternalStore } from 'react'

/**
 * Spec #38's "follow playback" preference as SHARED live state (spec #72 §6).
 *
 * `usePersistedBoolean` gives each call site its own `useState` over the
 * same localStorage key, so two mounted consumers never see each other's
 * writes. The transcript viewer's checkbox and the Now Playing sheet's
 * toggle must stay in step in the same tick, so this module holds one value
 * and notifies every subscriber. The key is the one the viewer has always
 * used — renaming it would drop every user's saved preference.
 */
export const FOLLOW_PLAYBACK_STORAGE_KEY = 'thestill:transcript:followPlayback'

const listeners = new Set<() => void>()
let value: boolean | null = null

function read(): boolean {
  if (value !== null) return value
  if (typeof window === 'undefined') return false
  try {
    value = window.localStorage.getItem(FOLLOW_PLAYBACK_STORAGE_KEY) === 'true'
  } catch {
    value = false
  }
  return value
}

export function setFollowPlayback(next: boolean): void {
  value = next
  try {
    window.localStorage.setItem(FOLLOW_PLAYBACK_STORAGE_KEY, next ? 'true' : 'false')
  } catch {
    // Safari private mode rejects writes; the in-memory value still applies.
  }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Test seam: forget the cached value so each test reads localStorage afresh. */
export function __resetFollowPlaybackForTests(): void {
  value = null
  listeners.clear()
}

export function useFollowPlayback(): [boolean, (next: boolean) => void] {
  const current = useSyncExternalStore(subscribe, read, () => false)
  const set = useCallback((next: boolean) => setFollowPlayback(next), [])
  return [current, set]
}
