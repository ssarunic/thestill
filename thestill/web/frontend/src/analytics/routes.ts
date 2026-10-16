// Spec #95 — page views report the route pattern, never the real URL: no
// slugs of private pages, no ids, no query strings.
import { matchPath } from 'react-router-dom'

// The signed-in app's routes (App.tsx). routes.test.ts keeps this in step.
export const APP_ROUTE_PATTERNS = [
  '/',
  '/inbox',
  '/podcasts',
  '/podcasts/:podcastSlug',
  '/podcasts/:podcastSlug/episodes/:episodeSlug',
  '/episodes',
  '/top',
  '/briefings',
  '/briefings/:briefingId',
  '/search',
  '/settings',
  '/entities/:entityType/:idSlug',
  '/status',
  '/queue',
  '/failed',
  '/analytics',
  '/login',
  '/terms',
  '/privacy',
] as const

/** The pattern ``pathname`` matches, or ``/other`` for anything else. */
export function routePattern(pathname: string): string {
  return APP_ROUTE_PATTERNS.find((pattern) => matchPath(pattern, pathname)) ?? '/other'
}
