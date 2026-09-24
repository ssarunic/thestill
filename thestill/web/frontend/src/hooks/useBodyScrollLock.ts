import { useEffect } from 'react'

/**
 * Locks page scroll behind a modal surface (overlay, phone sheet) while
 * `active`, restoring whatever `overflow` the body had before. Nested
 * locks compose: each restores the value it found, so a sheet opened
 * over the reader overlay hands the lock back to the overlay on close.
 */
export function useBodyScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active || typeof document === 'undefined') return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [active])
}
