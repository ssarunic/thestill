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

// Beyond this many screens a glide is a long blur of unrendered rows.
const FAR_JUMP_VIEWPORTS = 3

function scrollNodeIntoCenter(node: HTMLElement) {
  const reduceMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const far = typeof window !== 'undefined'
    && Math.abs(node.getBoundingClientRect().top) > window.innerHeight * FAR_JUMP_VIEWPORTS
  // 'instant', not 'auto': the document sets `scroll-behavior: smooth`, which
  // 'auto' defers to.
  node.scrollIntoView?.({ block: 'center', behavior: reduceMotion || far ? 'instant' : 'smooth' })
}

const SETTLE_POLL_MS = 60
const SETTLE_MAX_POLLS = 50
const SETTLE_MAX_CORRECTIONS = 10

/**
 * Keeps a jump honest. Rows off screen are laid out at a placeholder height
 * (`content-visibility: auto`, see index.css), so a far jump is aimed at an
 * estimate: the rows in between take their real height as they come near and
 * the scroll comes to rest short of the target, by thousands of pixels in a
 * long transcript. Once the scroll has stopped, re-aim until the row really
 * is on screen. Returns a cancel function; the visitor scrolling cancels too.
 */
function settleOnNode(node: HTMLElement, onCorrect: () => void): () => void {
  if (typeof window === 'undefined') return () => {}
  let timer: number | undefined
  let polls = 0
  let corrections = 0
  let lastTop = Number.NaN
  let topBeforeCorrection = Number.NaN
  const cancel = () => {
    window.clearTimeout(timer)
    window.removeEventListener('wheel', cancel)
    window.removeEventListener('touchmove', cancel)
    window.removeEventListener('keydown', onKeyDown)
  }
  const onKeyDown = (e: KeyboardEvent) => {
    if (SCROLL_KEYS.has(e.key)) cancel()
  }
  const poll = () => {
    polls += 1
    if (!node.isConnected || polls > SETTLE_MAX_POLLS) return cancel()
    const { top, height } = node.getBoundingClientRect()
    const moving = top !== lastTop
    lastTop = top
    if (!moving) {
      // At rest. On screen means the row lies wholly inside the viewport, or
      // (taller than it, or a neighbour of the middle) across its middle.
      const viewport = window.innerHeight
      const bottom = top + height
      const onScreen = (top >= 0 && bottom <= viewport) || (top <= viewport / 2 && bottom >= viewport / 2)
      // A re-aim that moved nothing has nowhere left to go (the row is at
      // the very start or end of what can scroll).
      const stuck = top === topBeforeCorrection
      if (onScreen || stuck || corrections >= SETTLE_MAX_CORRECTIONS) return cancel()
      corrections += 1
      topBeforeCorrection = top
      onCorrect()
      // Instant: a second glide would be aimed at an estimate again.
      node.scrollIntoView?.({ block: 'center', behavior: 'instant' })
    }
    timer = window.setTimeout(poll, SETTLE_POLL_MS)
  }
  window.addEventListener('wheel', cancel, { passive: true })
  window.addEventListener('touchmove', cancel, { passive: true })
  window.addEventListener('keydown', onKeyDown)
  timer = window.setTimeout(poll, SETTLE_POLL_MS)
  return cancel
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

  // Every scroll this hook makes — a follow step, Resume, a jump — goes
  // through here: the newest one wins (an older jump still settling would
  // pull the page back), and each is checked after it lands, because any of
  // them can cross the whole transcript (follow switched on far into an
  // episode, a deep link, a citation).
  const cancelSettle = useRef<(() => void) | null>(null)
  const aim = useCallback((node: HTMLElement) => {
    programmaticScrollAt.current = now()
    scrollNodeIntoCenter(node)
    cancelSettle.current?.()
    cancelSettle.current = settleOnNode(node, () => {
      programmaticScrollAt.current = now()
    })
  }, [])
  useEffect(() => () => cancelSettle.current?.(), [])

  const resume = useCallback(() => {
    setPausedUntil(0)
    const node = activeKey != null ? refs.current.get(activeKey) : undefined
    if (node) aim(node)
  }, [activeKey, aim])

  useEffect(() => {
    if (!enabled || activeKey == null) return
    if (pausedUntil > now()) return
    const node = refs.current.get(activeKey)
    if (!node) return
    aim(node)
  }, [activeKey, enabled, pausedUntil, aim])

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
    if (node) aim(node)
  }, [aim])

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
