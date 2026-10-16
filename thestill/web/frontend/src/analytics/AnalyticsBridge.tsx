import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { startAnalytics, stopAnalytics, track } from './client'
import { routePattern } from './routes'

/**
 * Spec #95 — starts usage events for a signed-in reader whose status came
 * with a key, and reports each page view as its route pattern.
 *
 * Mounted in App outside the page ``<Routes>``: those receive the inbox as
 * their location while the reader overlay is open, whereas this sees the
 * real URL, so opening an episode in the overlay counts as an episode view.
 */
export default function AnalyticsBridge() {
  const { user, analyticsKey } = useAuth()
  const { pathname } = useLocation()
  const key = user ? analyticsKey : null
  const userId = user?.id

  useEffect(() => {
    if (key && userId) startAnalytics(key, { id: userId })
    else stopAnalytics()
  }, [key, userId])

  useEffect(() => {
    if (key) track('$pageview', { path_kind: routePattern(pathname) })
  }, [key, pathname])

  return null
}
