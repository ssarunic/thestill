import { useEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'
import { useBackgroundLocation } from './useBackgroundLocation'

// Window scroll behaviour for every route, mounted once in ``Layout``:
//
//   PUSH to a new pathname  → start at the top
//   POP (Back/Forward)      → restore the offset recorded for that entry
//   same-pathname PUSH/REPLACE (filters, ``?view=transcript``, citation jumps)
//                           → leave the offset alone; the page owns it
//
// React Router (plain ``<Routes>``, not a data router) does no scroll
// restoration, and the browser's native restoration is unreliable in SPAs —
// on Back it fires before the page has re-rendered, so it lands at the top.
// Recording is keyed by ``location.key`` (unique per history entry) so
// Back/Forward restore while a fresh navigation to the same page starts at
// the top. Living in ``Layout`` rather than per page is what makes the
// convention hold for detail pages too (the 2026-09-07 People-row bug, PR
// #197, was a page that had opted out without noticing) — see
// docs/code-guidelines.md "Navigation invariants".
//
// The spec #52 reader overlay pushes an episode URL over the inbox while the
// inbox stays mounted and the body is scroll-locked: while a background
// location is present nothing here runs, so opening the overlay never moves
// the page beneath it, and closing it is a POP back to an entry whose offset
// is still on screen.
//
// Pages with their own scroll container (the overlay reader) keep their own
// hook (``useReadingPosition``); this one only ever touches the window.
const positions = new Map<string, number>()

export function useScrollRestoration(): void {
  const location = useLocation()
  const navType = useNavigationType()
  const inOverlay = useBackgroundLocation() != null
  const keyRef = useRef(location.key)
  keyRef.current = location.key
  const prevPathnameRef = useRef<string | null>(null)

  // Continuously record this entry's scroll offset. Recorded synchronously:
  // a Map.set per scroll event is free, and deferring it to a frame lost the
  // last offset whenever a click navigated away before that frame ran (the
  // browser's own scroll-into-view before the click is exactly such a scroll).
  useEffect(() => {
    const onScroll = () => {
      positions.set(keyRef.current, window.scrollY)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (inOverlay) return
    const prevPathname = prevPathnameRef.current
    prevPathnameRef.current = location.pathname

    if (navType === 'PUSH') {
      // A new page starts at the top; a same-page push (search params) keeps
      // whatever offset the page has arranged for itself.
      if (prevPathname !== null && prevPathname !== location.pathname) {
        window.scrollTo({ top: 0, behavior: 'instant' })
      }
      return
    }
    if (navType !== 'POP') return

    // The retry loop keeps re-applying the target until the page is tall
    // enough to reach it (cached content renders on the first commit; lazy
    // chunks and images add height a few frames later), capped so a
    // now-shorter page doesn't spin forever. ``behavior: 'instant'`` opts out
    // of the document's ``scroll-behavior: smooth``: Back should land where
    // the user was, not animate there, and an animated scroll would also
    // defeat the per-frame convergence check.
    const target = positions.get(location.key)
    if (!target) return
    let frames = 0
    let raf = requestAnimationFrame(function attempt() {
      window.scrollTo({ top: target, behavior: 'instant' })
      frames += 1
      if (Math.abs(window.scrollY - target) > 2 && frames < 30) {
        raf = requestAnimationFrame(attempt)
      }
    })
    return () => cancelAnimationFrame(raf)
  }, [location.key, location.pathname, navType, inOverlay])
}
