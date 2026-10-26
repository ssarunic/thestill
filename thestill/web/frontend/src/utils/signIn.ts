// Spec #102 — one sign-in page. Public buttons link to /login with where to
// come back to and, optionally, why; a Follow click survives the round trip
// as a pending follow in sessionStorage (set by a click on /login, never by
// a URL alone).
import { safeNextPath } from './safeNext'

export type SignInIntent = 'follow' | 'transcript' | 'summary'

const INTENTS: readonly SignInIntent[] = ['follow', 'transcript', 'summary']
const PENDING_FOLLOW_KEY = 'thestill.pendingFollow'
const PENDING_FOLLOW_MAX_AGE_MS = 15 * 60_000

/** The sign-in link for an action on a public page: the bare ``/login``
 * with ``next`` (where to come back to) and ``intent`` (why) in the URL
 * fragment. A fragment survives a Ctrl-click or "Open in new tab" like
 * any href, but is never sent to the server and does not make a distinct
 * URL for a crawler, so every page shares one robots-blocked ``/login``
 * instead of one ``/login?next=…`` each (Search Console listed 5,851 of
 * those on 2026-10-26). The signed-in app's own redirect for a protected
 * route still uses the query form, which ``/login`` also reads. */
export function signInPath(next: string, intent?: SignInIntent): string {
  const params = new URLSearchParams()
  const safe = safeNextPath(next)
  if (safe) params.set('next', safe)
  if (intent) params.set('intent', intent)
  const fragment = params.toString()
  return fragment ? `/login#${fragment}` : '/login'
}

export interface SignInRequest {
  next: string | null
  intent: SignInIntent | null
}

/** ``next`` and ``intent`` from a sign-in link's fragment (``#next=…&intent=…``),
 * each validated; a missing or malformed fragment yields nulls. */
export function parseSignInFragment(hash: string): SignInRequest {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash)
  return { next: safeNextPath(params.get('next')), intent: parseIntent(params.get('intent')) }
}

export function parseIntent(value: string | null): SignInIntent | null {
  return INTENTS.find((i) => i === value) ?? null
}

/** The show slug when ``next`` is a show page (``/podcasts/<slug>``). */
export function showSlugFromNext(next: string | null): string | null {
  const path = (next ?? '').split(/[?#]/)[0]
  const match = /^\/podcasts\/([a-z0-9][a-z0-9-]{0,110})\/?$/.exec(path)
  return match ? match[1] : null
}

// Pages a signed-out visitor can open (spec #94 PublicApp routes).
const PUBLIC_PATH = /^\/(?:top|terms|privacy|podcasts\/[^/]+(?:\/episodes\/[^/]+)?|entities\/[^/]+\/[^/]+)?\/?$/

/** Where "Not now" on /login goes: back to ``next`` when it is a public
 * page, else home (a signed-in path would only ask for sign-in again). */
export function notNowPath(next: string | null): string {
  return next && PUBLIC_PATH.test(next.split(/[?#]/)[0]) ? next : '/'
}

export function rememberPendingFollow(slug: string, now = Date.now()): void {
  try {
    window.sessionStorage.setItem(PENDING_FOLLOW_KEY, JSON.stringify({ slug, at: now }))
  } catch {
    // Storage blocked (private mode): the visitor presses Follow once more.
  }
}

/** True when a follow of ``slug`` was started on /login in this tab within
 * the last 15 minutes. Reading clears it, matched or not. */
export function consumePendingFollow(slug: string, now = Date.now()): boolean {
  try {
    const raw = window.sessionStorage.getItem(PENDING_FOLLOW_KEY)
    if (!raw) return false
    window.sessionStorage.removeItem(PENDING_FOLLOW_KEY)
    const pending = JSON.parse(raw) as { slug?: unknown; at?: unknown }
    const age = typeof pending.at === 'number' ? now - pending.at : -1
    return pending.slug === slug && age >= 0 && age <= PENDING_FOLLOW_MAX_AGE_MS
  } catch {
    return false
  }
}
