// Spec #94 — non-component helpers for the public pages (kept apart from
// ui.tsx so React fast refresh stays component-only).
import { useLocation } from 'react-router-dom'
import { episodePath, type PublicEntityType, type PublicQuote } from '../../api/public'
import { deepLinkParam } from '../../hooks/useDeepLinkSeek'
import { signInLink, type SignInIntent, type SignInLink } from '../../utils/signIn'

/** Spec #102 — the sign-in page link for an action on this page. Buttons
 * keep their usual names (Follow, Transcript); signed out, they lead to
 * /login, which offers the providers and returns to ``next`` (default:
 * this page, fragment included so a quote's ``#t=`` or a person's
 * ``#about=`` survives sign-in). The link is the bare ``/login`` with
 * ``next`` and ``intent`` in router state, so every page shares one URL. */
export function useSignIn(intent?: SignInIntent, next?: string): SignInLink {
  const location = useLocation()
  return signInLink(next ?? returnPath(location.pathname, location.search, location.hash), intent)
}

const EPISODE_PATH_RE = /^\/podcasts\/[^/]+\/episodes\/[^/]+\/?$/

/** Where sign-in brings a visitor back to. An episode opened from a quote
 * (``#t=``) comes back as ``?view=transcript&t=``: the signed-in reader
 * opens the summary unless ``view=transcript`` is set, and scrolls only the
 * transcript to ``t``. */
export function returnPath(pathname: string, search: string, hash: string): string {
  const seconds = deepLinkParam('', hash, 't')
  if (seconds == null || !EPISODE_PATH_RE.test(pathname)) return pathname + search + hash
  const params = new URLSearchParams(search)
  params.set('view', 'transcript')
  params.set('t', seconds)
  return `${pathname}?${params.toString()}`
}

const TYPE_LABEL: Record<PublicEntityType, string> = {
  person: 'Person',
  company: 'Company',
  product: 'Product',
  topic: 'Topic',
}

export function typeLabel(type: PublicEntityType) {
  return TYPE_LABEL[type] ?? 'Topic'
}

/** A quote's link: ``t`` is the reader's own deep-link parameter, so the
 * same URL lands on the passage whether or not the recipient is signed in.
 * It goes in the fragment so crawlers see one URL per episode, not one per
 * quoted minute (see ``deepLinkParam``). */
export function quoteUrl(quote: PublicQuote) {
  return `${episodePath(quote.podcast.slug, quote.episode.slug)}#t=${quote.start_seconds}`
}

/** Groups consecutive quotes by episode (the API returns newest episode first). */
export function groupByEpisode(quotes: PublicQuote[]) {
  const groups: { key: string; quotes: PublicQuote[] }[] = []
  for (const q of quotes) {
    const key = `${q.podcast.slug}/${q.episode.slug}`
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.quotes.push(q)
    else groups.push({ key, quotes: [q] })
  }
  return groups
}
