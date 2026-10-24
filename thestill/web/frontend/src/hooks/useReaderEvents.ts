import { useEffect, useRef, type RefObject } from 'react'
import { track } from '../analytics/client'

type ReaderTab = 'summary' | 'transcript'

const DEPTHS = [25, 50, 75, 100] as const

/**
 * Spec #95 usage events for the episode reader: ``reader_tab`` once per tab
 * shown (landing included) and ``read_depth`` once per tab and milestone,
 * both per episode. Depth is how far the bottom of the viewport has reached
 * through the scroll container, measured on scroll only, so a page that
 * fits the screen sends no depth until the reader scrolls. ``ready`` holds
 * depth back until the tab's content has rendered (a loading tab is short).
 * Never the episode, the show or any text.
 */
export function useReaderEvents({
  episodeId,
  tab,
  ready,
  scrollContainerRef,
}: {
  episodeId: string | undefined
  tab: ReaderTab
  ready: boolean
  scrollContainerRef?: RefObject<HTMLElement | null>
}) {
  const sent = useRef<{ episodeId: string | undefined; keys: Set<string> }>({ episodeId: undefined, keys: new Set() })

  useEffect(() => {
    if (!episodeId) return
    if (sent.current.episodeId !== episodeId) sent.current = { episodeId, keys: new Set() }
    const key = `tab:${tab}`
    if (sent.current.keys.has(key)) return
    sent.current.keys.add(key)
    track('reader_tab', { tab })
  }, [episodeId, tab])

  useEffect(() => {
    if (!episodeId || !ready) return
    const container = scrollContainerRef?.current ?? null
    const target: HTMLElement | Window = container ?? window
    let frame = 0
    const measure = () => {
      frame = 0
      const scrolled = container ? container.scrollTop + container.clientHeight : window.scrollY + window.innerHeight
      const height = container ? container.scrollHeight : document.documentElement.scrollHeight
      if (height <= 0) return
      const percent = (scrolled / height) * 100
      for (const depth of DEPTHS) {
        // The last few pixels rarely scroll into view exactly.
        if (percent < (depth === 100 ? 98 : depth)) break
        const key = `${tab}:${depth}`
        if (sent.current.keys.has(key)) continue
        sent.current.keys.add(key)
        track('read_depth', { tab, percent: depth })
      }
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure)
    }
    target.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      target.removeEventListener('scroll', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [episodeId, tab, ready, scrollContainerRef])
}
