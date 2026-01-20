import { useEffect, useCallback, useRef } from 'react'

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
 * Position is automatically restored when the episode ID is set.
 * Scroll position is saved with debouncing to avoid excessive writes.
 */
export function useReadingPosition(episodeId: string | undefined) {
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

  // Auto-restore position when episode ID changes
  useEffect(() => {
    if (!episodeId) return

    // Don't restore if we already restored for this episode
    if (hasRestoredRef.current === episodeId) return

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
  }, [episodeId])

  return {
    clearPosition,
  }
}
