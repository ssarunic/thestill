import { useEffect, useCallback, useRef } from 'react'
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
 */
export function useReadingPosition(episodeId: string | undefined) {
  const location = useLocation()
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isRestoringRef = useRef(false)
  const hasRestoredRef = useRef<string | null>(null)

  // Save position to localStorage
  const savePosition = useCallback(() => {
    if (!episodeId || isRestoringRef.current) return

    const key = `${STORAGE_PREFIX}${episodeId}`
    const scrollHeight = document.documentElement.scrollHeight - window.innerHeight
    if (scrollHeight <= 0) return

    const scrollPercent = window.scrollY / scrollHeight

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
  }, [episodeId])

  // Clear saved position
  const clearPosition = useCallback(() => {
    if (!episodeId) return
    localStorage.removeItem(`${STORAGE_PREFIX}${episodeId}`)
  }, [episodeId])

  // Set up debounced scroll listener
  useEffect(() => {
    if (!episodeId) return

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

    window.addEventListener('scroll', handleScroll, { passive: true })

    return () => {
      window.removeEventListener('scroll', handleScroll)
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }
    }
  }, [episodeId, savePosition])

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
      window.scrollTo({ top: 0, behavior: 'instant' })
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
          const scrollHeight = document.documentElement.scrollHeight - window.innerHeight
          if (scrollHeight > 0) {
            const targetScroll = data.scrollPercent * scrollHeight
            window.scrollTo({
              top: targetScroll,
              behavior: 'instant',
            })
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
  }, [episodeId, location.state])

  return {
    clearPosition,
  }
}
