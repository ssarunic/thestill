// Spec #116 Phase 2 — what holding (or not holding) `drift.align` means for
// the whole app, not just the reader:
//
// - held: the player may load audio with `crossorigin` for hosts known to
//   allow it, so the service worker can check what the element is served;
// - not held (signed out, not an admin, or DRIFT_ALIGNMENT_ENABLED=false):
//   no element gets the attribute, and the service worker is told to stand
//   down, unregistered, and left behind by one reload. This is the kill
//   switch reaching browsers that already have the worker.
import { useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { setCrossOriginLoadingEnabled } from './corsVerdict'
import { listenForMediaReports, reloadOnceUncontrolled, removeDriftServiceWorker } from './swClient'

export function useDriftSession(): void {
  const { can, isLoading } = useAuth()
  const held = can('drift.align')
  useEffect(() => {
    // Until the session is known, change nothing: a held permission must
    // not lose its worker on every page load.
    if (isLoading) return
    // Both are remembered or attached for the next page load too, where
    // the player restores a source before this effect has run.
    setCrossOriginLoadingEnabled(held)
    if (held) listenForMediaReports()
    else {
      // A page the worker already controls stays controlled after it is
      // unregistered; one reload ends that.
      void removeDriftServiceWorker().then((controlled) => {
        if (controlled) reloadOnceUncontrolled()
      })
    }
  }, [held, isLoading])
}
