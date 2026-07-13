import { useEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

// Per-entry scroll-position memory for scrollable list/index pages.
//
// React Router (plain `<Routes>`, not a data router) does no scroll
// restoration, and the browser's native restoration is unreliable in SPAs — on
// Back it fires before the list has re-rendered, so it lands at the top. This
// hook records the window scroll offset for the active history entry and, when
// the user returns to that entry via Back/Forward, restores it — retrying
// across animation frames so a list whose height grows as (cached) data and
// lazy images settle still ends up at the right spot.
//
// Keyed by `location.key` (unique per history entry) so Back/Forward restore
// while a fresh navigation to the same page starts at the top. Apply to every
// scrollable list page so returning from a detail page keeps the user where
// they were (app-wide convention — see the [[feedback_list_pages_preserve_filters_scroll]] rule).
const positions = new Map<string, number>()

export function useScrollRestoration(): void {
  const location = useLocation()
  const navType = useNavigationType()
  const keyRef = useRef(location.key)
  keyRef.current = location.key

  // Continuously record this entry's scroll offset (rAF-throttled so we don't
  // hammer the Map on every scroll event).
  useEffect(() => {
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        positions.set(keyRef.current, window.scrollY)
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  // On Back/Forward (POP) to a remembered entry, restore its offset. The retry
  // loop keeps re-applying the target until the page is tall enough to reach it
  // (cached content renders on the first commit; lazy images may add height a
  // few frames later), capped so a now-shorter list doesn't spin forever.
  useEffect(() => {
    if (navType !== 'POP') return
    const target = positions.get(location.key)
    if (!target) return
    let frames = 0
    let raf = requestAnimationFrame(function attempt() {
      window.scrollTo(0, target)
      frames += 1
      if (Math.abs(window.scrollY - target) > 2 && frames < 30) {
        raf = requestAnimationFrame(attempt)
      }
    })
    return () => cancelAnimationFrame(raf)
  }, [location.key, navType])
}
