import { useCallback, useRef } from 'react'
import { readPersistedRate, writePersistedRate } from '../utils/playbackRate'

/**
 * Spec #72 §5 — the persisted rate preference, for use INSIDE
 * `PlayerProvider`. A ref rather than state: the provider reads it
 * synchronously inside `play()` / `playYouTube()` (user-gesture stack), and
 * the reactive value every consumer renders is already
 * `PlayerContextValue.playbackRate`, mirrored from engine `ratechange`
 * events. Persisting here keeps the preference the user's explicit choice —
 * an engine snapping to a supported rate (YouTube) does not overwrite it.
 */
export function usePlayerRatePreference() {
  const preferredRateRef = useRef<number | null>(null)
  if (preferredRateRef.current === null) preferredRateRef.current = readPersistedRate()

  const persistRate = useCallback((rate: number) => {
    preferredRateRef.current = rate
    writePersistedRate(rate)
  }, [])

  return { preferredRateRef: preferredRateRef as React.RefObject<number>, persistRate }
}
