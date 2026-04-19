import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/**
 * Hook that scrolls an element registered in its ref map into view each time
 * the active key changes, provided follow-mode is enabled. User-initiated
 * scrolling temporarily suspends follow so we don't fight the reader.
 *
 * Usage:
 *
 *   const follow = useAutoScrollFollow({ activeKey: activeId, enabled })
 *   <div ref={follow.registerRef(id)}>...</div>
 *   {follow.userScrolledAway && <button onClick={follow.resume}>Resume</button>}
 */
export interface UseAutoScrollFollowOptions {
  activeKey: string | number | null
  enabled: boolean
  // How long (ms) user scroll silences the auto-follow. Default 8s.
  pauseAfterUserScrollMs?: number
}

export interface UseAutoScrollFollowResult {
  registerRef: (key: string | number) => (el: HTMLElement | null) => void
  userScrolledAway: boolean
  resume: () => void
}

export function useAutoScrollFollow({
  activeKey,
  enabled,
  pauseAfterUserScrollMs = 8000,
}: UseAutoScrollFollowOptions): UseAutoScrollFollowResult {
  const refs = useRef<Map<string | number, HTMLElement>>(new Map())
  const [pausedUntil, setPausedUntil] = useState(0)
  const programmaticScrollAt = useRef(0)

  const registerRef = useMemo(() => {
    return (key: string | number) => (el: HTMLElement | null) => {
      if (el) refs.current.set(key, el)
      else refs.current.delete(key)
    }
  }, [])

  const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

  const resume = useCallback(() => {
    setPausedUntil(0)
    programmaticScrollAt.current = now()
    const node = activeKey != null ? refs.current.get(activeKey) : undefined
    if (node) {
      const reduceMotion = typeof window !== 'undefined'
        && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      node.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
    }
  }, [activeKey])

  // Scroll the active segment into view whenever it changes while follow
  // is on.
  useEffect(() => {
    if (!enabled || activeKey == null) return
    if (pausedUntil > now()) return
    const node = refs.current.get(activeKey)
    if (!node) return
    const reduceMotion = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    programmaticScrollAt.current = now()
    node.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
  }, [activeKey, enabled, pausedUntil])

  // Detect user-driven scroll via wheel/touch/keyboard and pause follow.
  useEffect(() => {
    if (!enabled) return
    const markUserScrolled = () => {
      // Ignore scroll events that we triggered ourselves — allow a small
      // window for smooth-scrolling to settle before counting anything as
      // user input.
      if (now() - programmaticScrollAt.current < 600) return
      setPausedUntil(now() + pauseAfterUserScrollMs)
    }
    window.addEventListener('wheel', markUserScrolled, { passive: true })
    window.addEventListener('touchmove', markUserScrolled, { passive: true })
    window.addEventListener('keydown', (e) => {
      if (['PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', ' ', 'End', 'Home'].includes(e.key)) {
        markUserScrolled()
      }
    })
    return () => {
      window.removeEventListener('wheel', markUserScrolled)
      window.removeEventListener('touchmove', markUserScrolled)
    }
  }, [enabled, pauseAfterUserScrollMs])

  const userScrolledAway = pausedUntil > now()

  return { registerRef, userScrolledAway, resume }
}

/** Read+write a boolean preference to localStorage. */
export function usePersistedBoolean(key: string, initial: boolean): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    if (typeof window === 'undefined') return initial
    try {
      const raw = window.localStorage.getItem(key)
      if (raw === null) return initial
      return raw === 'true'
    } catch {
      return initial
    }
  })
  const write = useCallback(
    (next: boolean) => {
      setValue(next)
      try {
        window.localStorage.setItem(key, next ? 'true' : 'false')
      } catch {
        // ignore — Safari private mode etc.
      }
    },
    [key],
  )
  return [value, write]
}
