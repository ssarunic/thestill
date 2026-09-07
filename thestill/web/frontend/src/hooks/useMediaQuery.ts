import { useCallback, useSyncExternalStore } from 'react'

/**
 * Subscribe to a CSS media query. ``fallback`` is returned where
 * ``matchMedia`` is unavailable (tests, SSR).
 */
export function useMediaQuery(query: string, fallback = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
      const mql = window.matchMedia(query)
      mql.addEventListener?.('change', onChange)
      return () => mql.removeEventListener?.('change', onChange)
    },
    [query],
  )
  const getSnapshot = useCallback(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return fallback
    return window.matchMedia(query).matches
  }, [query, fallback])
  return useSyncExternalStore(subscribe, getSnapshot, () => fallback)
}

/** Tailwind's ``sm`` breakpoint — the shell swaps its fixed mobile header for the sidebar here. */
export function useIsSmUp(fallback = true): boolean {
  return useMediaQuery('(min-width: 640px)', fallback)
}
