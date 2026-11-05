import { useCallback, useEffect, useState, type RefObject } from 'react'

// Sub-pixel slack: zoomed layouts can leave scrollTop a fraction short of
// the true end, which would otherwise keep the "more below" cue on forever.
const END_SLACK_PX = 2

/**
 * Whether a scroll container has content hidden below its visible bottom
 * edge. Drives a "more below" cue on independently scrolling panels (the
 * episode reader's entity rail), where macOS hides the scrollbar until the
 * reader scrolls. Re-measures on scroll and whenever the container or its
 * content resizes (window resize, a "Show all" expander, late data).
 */
export function useCanScrollDown(ref: RefObject<HTMLElement | null>): boolean {
  const [canScrollDown, setCanScrollDown] = useState(false)

  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    setCanScrollDown(el.scrollHeight - el.clientHeight - el.scrollTop > END_SLACK_PX)
  }, [ref])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(measure)
      observer.observe(el)
      for (const child of Array.from(el.children)) observer.observe(child)
    }
    return () => {
      el.removeEventListener('scroll', measure)
      observer?.disconnect()
    }
  }, [ref, measure])

  return canScrollDown
}
