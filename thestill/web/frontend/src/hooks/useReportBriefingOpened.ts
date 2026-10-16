import { useEffect, useRef } from 'react'
import { useMarkBriefingOpened } from './useApi'

/**
 * Spec #95 — tell the server the reader opened a briefing, once per id.
 *
 * Pass the id only once the briefing has loaded, so a missing briefing
 * never posts. The ref survives StrictMode's double effect and re-arms when
 * the page moves to another briefing. The server keeps the first time, so
 * a repeat on a later visit is harmless; failures are swallowed.
 */
export function useReportBriefingOpened(briefingId: string | null) {
  const { mutate } = useMarkBriefingOpened()
  const reported = useRef<string | null>(null)
  useEffect(() => {
    if (!briefingId || reported.current === briefingId) return
    reported.current = briefingId
    mutate(briefingId)
  }, [briefingId, mutate])
}
