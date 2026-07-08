import { useEffect, useCallback, useRef, type RefObject } from 'react'
import { useLocation } from 'react-router-dom'

interface ReadingPositionData {
  scrollPercent: number
  timestamp: number
}

const STORAGE_PREFIX = 'reading-position-'
const POSITION_EXPIRY_DAYS = 30

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
      }
    }
  }, [episodeId, savePosition, scrollContainerRef])

  // Auto-restore position only when navigating back (POP navigation)
  // Fresh navigations (clicking links) should start at the top
  useEffect(() => {
    if (!episodeId) return

    // Don't restore if we already restored for this episode
    if (hasRestoredRef.current === episodeId) return

    // Only restore position on back/forward navigation (POP), not on fresh link clicks (PUSH)
    // location.state?.fromBack is set by the browser on back navigation
    // We use the history API's navigation type when available
    const navigationType = (window.performance?.getEntriesByType?.('navigation')?.[0] as PerformanceNavigationTiming)?.type
    const isBackNavigation = navigationType === 'back_forward' || location.state?.restoreScroll

    if (!isBackNavigation) {
      // Fresh navigation - scroll to top and don't restore
      scrollToTop(0)
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
  }, [episodeId, location.state, getMaxScroll, scrollToTop])

  return {
    clearPosition,
  }
}
