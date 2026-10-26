// Spec #102 — one sign-in page. Public buttons link to /login with where to
// come back to and, optionally, why; a Follow click survives the round trip
// as a pending follow in sessionStorage (set by a click on /login, never by
// a URL alone).
import { safeNextPath } from './safeNext'

export type SignInIntent = 'follow' | 'transcript' | 'summary'

const INTENTS: readonly SignInIntent[] = ['follow', 'transcript', 'summary']
const PENDING_FOLLOW_KEY = 'thestill.pendingFollow'
const PENDING_FOLLOW_MAX_AGE_MS = 15 * 60_000

export function signInPath(next: string, intent?: SignInIntent): string {
  const params = new URLSearchParams()
  const safe = safeNextPath(next)
  if (safe) params.set('next', safe)
  if (intent) params.set('intent', intent)
  const qs = params.toString()
  return qs ? `/login?${qs}` : '/login'
}

/** What a public page's sign-in button navigates with. */
export interface SignInLink {
  /** Always the bare ``/login``: one URL for every page, so a crawler that
   * follows the link finds one robots-blocked address, not one per page
   * (Search Console listed 5,851 ``/login?next=…`` URLs on 2026-10-26). */
  to: '/login'
  /** Where to come back to and why; read by /login from router state. */
  state: SignInState
}

export interface SignInState {
  next?: string
  intent?: SignInIntent
}

export function signInLink(next: string, intent?: SignInIntent): SignInLink {
  const state: SignInState = {}
  const safe = safeNextPath(next)
  if (safe) state.next = safe
  if (intent) state.intent = intent
  return { to: '/login', state }
}

/** The state a sign-in link carried, when the router state is one. */
export function parseSignInState(value: unknown): SignInState {
  if (!value || typeof value !== 'object') return {}
  const raw = value as { next?: unknown; intent?: unknown }
  const state: SignInState = {}
  const next = typeof raw.next === 'string' ? safeNextPath(raw.next) : null
  if (next) state.next = next
  const intent = typeof raw.intent === 'string' ? parseIntent(raw.intent) : null
  if (intent) state.intent = intent
  return state
}

/** The page the visitor came from, when it is on this site: the return
 * path for a sign-in link clicked before the app took over (the plain
 * ``/login`` anchor in the server-written page carries no ``next``). */
export function referrerPath(referrer: string, origin: string): string | null {
  if (!referrer || !origin) return null
  try {
    const url = new URL(referrer)
    if (url.origin !== origin) return null
    return safeNextPath(url.pathname + url.search + url.hash)
  } catch {
    return null
  }
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
