// Spec #94 — non-component helpers for the public pages (kept apart from
// ui.tsx so React fast refresh stays component-only).
import { useAuth } from '../../contexts/AuthContext'
import { episodePath, type PublicEntityType, type PublicQuote } from '../../api/public'

/** Starts Google sign-in and returns the visitor to the current page. */
export function useSignIn() {
  const { login } = useAuth()
  return () => login(window.location.pathname + window.location.search)
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

export function quoteUrl(quote: PublicQuote) {
  return `${episodePath(quote.podcast.slug, quote.episode.slug)}?q=${quote.start_seconds}`
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
