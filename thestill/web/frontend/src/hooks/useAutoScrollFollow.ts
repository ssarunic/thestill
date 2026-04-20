import { useCallback, useEffect, useRef, useState } from 'react'

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
  scrollToKey: (key: string | number) => void
}

const SCROLL_KEYS = new Set(['PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', ' ', 'End', 'Home'])

function now() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function scrollNodeIntoCenter(node: HTMLElement) {
  const reduceMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  node.scrollIntoView?.({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
}

export function useAutoScrollFollow({
  activeKey,
  enabled,
  pauseAfterUserScrollMs = 8000,
}: UseAutoScrollFollowOptions): UseAutoScrollFollowResult {
  const refs = useRef<Map<string | number, HTMLElement>>(new Map())
  // Cache of per-key ref callbacks so each segment gets a stable identity
  // across parent re-renders — avoids React teardown+setup per tick.
  const refCallbacks = useRef<Map<string | number, (el: HTMLElement | null) => void>>(new Map())
  const [pausedUntil, setPausedUntil] = useState(0)
  const programmaticScrollAt = useRef(0)

  const registerRef = useCallback((key: string | number) => {
    const existing = refCallbacks.current.get(key)
    if (existing) return existing
    const callback = (el: HTMLElement | null) => {
      if (el) refs.current.set(key, el)
      else refs.current.delete(key)
    }
    refCallbacks.current.set(key, callback)
    return callback
  }, [])

  const resume = useCallback(() => {
    setPausedUntil(0)
    programmaticScrollAt.current = now()
    const node = activeKey != null ? refs.current.get(activeKey) : undefined
    if (node) scrollNodeIntoCenter(node)
  }, [activeKey])

  useEffect(() => {
    if (!enabled || activeKey == null) return
    if (pausedUntil > now()) return
    const node = refs.current.get(activeKey)
    if (!node) return
    programmaticScrollAt.current = now()
    scrollNodeIntoCenter(node)
  }, [activeKey, enabled, pausedUntil])

  useEffect(() => {
    if (!enabled) return
    // Allow a small window for our own smooth-scroll to settle before
    // counting any scroll event as user input.
    const markUserScrolled = () => {
      if (now() - programmaticScrollAt.current < 600) return
      setPausedUntil(now() + pauseAfterUserScrollMs)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (SCROLL_KEYS.has(e.key)) markUserScrolled()
    }
    window.addEventListener('wheel', markUserScrolled, { passive: true })
    window.addEventListener('touchmove', markUserScrolled, { passive: true })
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('wheel', markUserScrolled)
      window.removeEventListener('touchmove', markUserScrolled)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [enabled, pauseAfterUserScrollMs])

  const scrollToKey = useCallback((key: string | number) => {
    const node = refs.current.get(key)
    if (!node) return
    programmaticScrollAt.current = now()
    scrollNodeIntoCenter(node)
  }, [])

  const userScrolledAway = pausedUntil > now()

  return { registerRef, userScrolledAway, resume, scrollToKey }
}

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
        // Safari private mode rejects writes; fall through.
      }
    },
    [key],
  )
  return [value, write]
}
