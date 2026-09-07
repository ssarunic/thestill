import { useCallback, useEffect, useState, type RefObject } from 'react'

export interface CollapsingHeader {
  /** Attach to the page title (``PageHero``'s ``titleRef``). */
  titleRef: (el: HTMLElement | null) => void
  /** True once the title has scrolled above the top edge of the scroll area. */
  collapsed: boolean
}

/**
 * Spec #76 §3.7 — detect when a detail page's title leaves the viewport so
 * the host can show its collapsed bar. The reader only detects; the host
 * renders (the overlay swaps its header, the page pins a sticky bar).
 *
 * ``scrollContainerRef`` is the overlay's scroll div or undefined for the
 * window. ``topOffset`` is the height of any fixed chrome above the scroll
 * area (the 56 px mobile shell header in page mode), so the title counts as
 * gone once it slides under that chrome, not the window edge.
 *
 * The title is taken as a callback ref rather than a ``RefObject`` because
 * it mounts after the loading skeleton; the observer must attach when the
 * element appears, not when the hook first runs.
 */
export function useCollapsingHeader(
  scrollContainerRef: RefObject<HTMLElement | null> | undefined,
  topOffset: number,
): CollapsingHeader {
  const [titleEl, setTitleEl] = useState<HTMLElement | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const titleRef = useCallback((el: HTMLElement | null) => setTitleEl(el), [])

  useEffect(() => {
    if (!titleEl || typeof IntersectionObserver === 'undefined') {
      return
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        // "Not intersecting" alone would also fire while the title sits
        // below the viewport; only an exit above the top edge collapses.
        const rootTop = entry.rootBounds?.top ?? topOffset
        setCollapsed(!entry.isIntersecting && entry.boundingClientRect.bottom <= rootTop)
      },
      {
        root: scrollContainerRef?.current ?? null,
        rootMargin: `-${topOffset}px 0px 0px 0px`,
        threshold: 0,
      },
    )
    observer.observe(titleEl)
    return () => {
      observer.disconnect()
      // A title that unmounts (episode swap back to the skeleton) is never
      // "collapsed"; a re-observe fires its initial entry straight away.
      setCollapsed(false)
    }
  }, [titleEl, scrollContainerRef, topOffset])

  return { titleRef, collapsed }
}
