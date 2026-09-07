import { useEffect, useCallback, useRef, type RefObject } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

interface ReadingPositionData {
  scrollPercent: number
  timestamp: number
}

const STORAGE_PREFIX = 'reading-position-'
const POSITION_EXPIRY_DAYS = 30

// History entries (``location.key``) the reader has already been mounted on
// in this session. Coming back to one of them via Back/Forward is a return
// and restores the saved position; a fresh entry starts at the top. Same
// per-entry convention as ``useScrollRestoration`` in ``Layout``.
const seenEntries = new Set<string>()

// A browser-level back/forward document load restores the reader's saved
// position once, on the first reader mount of that document; later mounts in
// the same document are in-app navigations and must not read it again.
let documentBackConsumed = false

/**
 * Hook to persist and restore reading position for an episode.
 * Saves scroll position as percentage (responsive across screen sizes).
 *
 * Position is only restored when navigating back (browser back/forward),
 * not when clicking a link to navigate to the page fresh.
 * Scroll position is saved with debouncing to avoid excessive writes.
 *
 * Spec #52 — the reader can render inside an overlay that scrolls its own
 * div rather than the window. Pass `scrollContainerRef` to track/restore
 * against that element; omitted, the window remains the scroll container
 * (standalone episode page behavior, unchanged).
 */
export function useReadingPosition(
  episodeId: string | undefined,
  scrollContainerRef?: RefObject<HTMLElement | null>,
) {
  const location = useLocation()
  const navigationType = useNavigationType()
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isRestoringRef = useRef(false)
  const hasRestoredRef = useRef<string | null>(null)

  // The three scroll primitives, branched once on container-vs-window so
  // the save/restore logic below stays container-agnostic.
  const getScrollTop = useCallback(() => {
    const el = scrollContainerRef?.current
    return el ? el.scrollTop : window.scrollY
  }, [scrollContainerRef])

  const getMaxScroll = useCallback(() => {
    const el = scrollContainerRef?.current
    return el
      ? el.scrollHeight - el.clientHeight
      : document.documentElement.scrollHeight - window.innerHeight
  }, [scrollContainerRef])

  const scrollToTop = useCallback(
    (top: number) => {
      const el = scrollContainerRef?.current
      if (el) {
        el.scrollTo({ top, behavior: 'instant' })
      } else {
        window.scrollTo({ top, behavior: 'instant' })
      }
    },
    [scrollContainerRef],
  )

  // Save position to localStorage
  const savePosition = useCallback(() => {
    if (!episodeId || isRestoringRef.current) return

    const key = `${STORAGE_PREFIX}${episodeId}`
    const scrollHeight = getMaxScroll()
    if (scrollHeight <= 0) return

    const scrollPercent = getScrollTop() / scrollHeight

    // Only save if user has scrolled past initial position
    if (scrollPercent < 0.01) return

    const data: ReadingPositionData = {
      scrollPercent,
      timestamp: Date.now(),
    }

    try {
      localStorage.setItem(key, JSON.stringify(data))
    } catch {
      // localStorage might be full or unavailable
    }
  }, [episodeId, getMaxScroll, getScrollTop])

  // Clear saved position
  const clearPosition = useCallback(() => {
    if (!episodeId) return
    localStorage.removeItem(`${STORAGE_PREFIX}${episodeId}`)
  }, [episodeId])

  // Set up debounced scroll listener
  useEffect(() => {
    if (!episodeId) return

    const target: HTMLElement | Window = scrollContainerRef?.current ?? window

    const handleScroll = () => {
      // Clear any pending save
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }

      // Debounce save by 500ms
      saveTimeoutRef.current = setTimeout(() => {
        savePosition()
      }, 500)
    }

    target.addEventListener('scroll', handleScroll, { passive: true })

    return () => {
      target.removeEventListener('scroll', handleScroll)
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
        // A link tapped inside the debounce window (scroll, then tap a
        // person) must not lose the position it was about to save. The
        // old page is still laid out when this cleanup runs.
        savePosition()
      }
    }
  }, [episodeId, savePosition, scrollContainerRef])

  // Auto-restore position only when navigating back (POP navigation)
  // Fresh navigations (clicking links) should start at the top
  useEffect(() => {
    if (!episodeId) return

    // Don't restore if we already restored for this episode
    if (hasRestoredRef.current === episodeId) return

    // Which navigations restore:
    // - Overlay (own scroll container): an in-app Back/Forward to a history
    //   entry this reader was already mounted on (router POP + seen key —
    //   the router reports POP for the very first render too, so the key
    //   check is what separates a return from a first visit). Fresh entries
    //   start the container at the top.
    // - Page mode (window): in-session Back is ``useScrollRestoration`` in
    //   ``Layout``'s job (exact offset, retry loop), and so is scrolling a
    //   fresh page to the top. This hook only restores the cross-session
    //   position on a browser-level back/forward document load.
    const ownsScroll = !!scrollContainerRef
    const documentNavigation = (
      window.performance?.getEntriesByType?.('navigation')?.[0] as PerformanceNavigationTiming | undefined
    )?.type
    const isDocumentBack = documentNavigation === 'back_forward' && !documentBackConsumed
    documentBackConsumed = true
    const isReturn = navigationType === 'POP' && seenEntries.has(location.key)
    seenEntries.add(location.key)
    const isBackNavigation = ownsScroll
      ? isReturn || isDocumentBack || location.state?.restoreScroll
      : isDocumentBack || location.state?.restoreScroll

    if (!isBackNavigation) {
      if (ownsScroll) scrollToTop(0)
      hasRestoredRef.current = episodeId
      return
    }

    const key = `${STORAGE_PREFIX}${episodeId}`

    try {
      const stored = localStorage.getItem(key)
      if (!stored) return

      const data: ReadingPositionData = JSON.parse(stored)

      // Check if position is expired
      const ageInDays = (Date.now() - data.timestamp) / (1000 * 60 * 60 * 24)
      if (ageInDays > POSITION_EXPIRY_DAYS) {
        localStorage.removeItem(key)
        return
      }

      // Mark as restored before actually restoring
      hasRestoredRef.current = episodeId
      isRestoringRef.current = true

      // Small delay to ensure content has rendered
      const timer = setTimeout(() => {
        requestAnimationFrame(() => {
          const scrollHeight = getMaxScroll()
          if (scrollHeight > 0) {
            const targetScroll = data.scrollPercent * scrollHeight
            scrollToTop(targetScroll)
          }

          // Allow saving again after restoration completes
          setTimeout(() => {
            isRestoringRef.current = false
          }, 100)
        })
      }, 150)

      return () => clearTimeout(timer)
    } catch {
      // Invalid stored data
    }
  }, [episodeId, location.key, location.state, navigationType, getMaxScroll, scrollToTop])

  return {
    clearPosition,
  }
}
