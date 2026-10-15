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

/** A quote's link: ``?t=`` is the reader's own deep-link parameter, so the
 * same URL lands on the passage whether or not the recipient is signed in. */
export function quoteUrl(quote: PublicQuote) {
  return `${episodePath(quote.podcast.slug, quote.episode.slug)}?t=${quote.start_seconds}`
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

const LEAD_SECTION = /\b(gist|key takeaways?|takeaways)\b/i

/**
 * Splits a summary for the public pages: the gist and key takeaways lead,
 * the other sections (timeline, quotes, drama, blog ideas…) wait behind a
 * "full summary" button. Section numbers are dropped, since the lead skips
 * some. A summary with neither heading leads with its first section.
 */
export function splitSummary(markdown: string): { lead: string; rest: string; restTitles: string[] } {
  const preamble: string[] = []
  const sections: { title: string; lines: string[] }[] = []
  for (const line of markdown.split('\n')) {
    const heading = /^##\s+(?:\d+\.\s+)?(.*)$/.exec(line)
    if (heading) sections.push({ title: heading[1].trim(), lines: [`## ${heading[1].trim()}`] })
    else if (sections.length) sections[sections.length - 1].lines.push(line)
    else preamble.push(line)
  }
  let leadIdx = sections.flatMap((s, i) => (LEAD_SECTION.test(s.title) ? [i] : []))
  if (leadIdx.length === 0 && sections.length) leadIdx = [0]
  const join = (parts: { lines: string[] }[]) => parts.map((s) => s.lines.join('\n')).join('\n')
  const rest = sections.filter((_, i) => !leadIdx.includes(i))
  return {
    lead: [...preamble, join(sections.filter((_, i) => leadIdx.includes(i)))].join('\n').trim(),
    rest: join(rest).trim(),
    restTitles: rest.map((s) => s.title.replace(/^[^\p{L}\p{N}"]+/u, '')),
  }
}
