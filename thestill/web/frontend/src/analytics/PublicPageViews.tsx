import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * Spec #95 — counts public page views without any third-party code. The
 * public site navigates in the browser after the first load, so each route
 * change posts the path to our own server, which decides the page kind,
 * drops bots and forwards to PostHog. The external referrer only matters on
 * the first view. Fire-and-forget; failures are ignored.
 */
export default function PublicPageViews() {
  const { pathname } = useLocation()
  const first = useRef(true)

  useEffect(() => {
    if (pathname === '/login') return
    const referrer = first.current ? document.referrer || null : null
    first.current = false
    fetch('/api/public/pageview', {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: pathname, referrer }),
    }).catch(() => {})
  }, [pathname])

  return null
}
