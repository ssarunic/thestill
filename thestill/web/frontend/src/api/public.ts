// Spec #94 — client for the signed-out public API under /api/public.
// Shapes mirror thestill/web/routes/api_public.py, which builds every
// response from an allowlist: no pipeline state, feed URLs or per-user flags.

export type PublicEntityType = 'person' | 'company' | 'product' | 'topic'

export interface PublicEntityRef {
  name: string
  type: PublicEntityType
  slug: string
  /** Wikimedia image when the entity has one (people: a portrait). */
  image_url?: string | null
}

export interface PublicPodcast {
  slug: string
  title: string
  description: string | null
  author: string | null
  image_url: string | null
  explicit: boolean | null
  copyright: string | null
  website_url: string | null
  primary_category: string | null
  episodes_count: number
  summarised_count: number
  latest_pub_date: string | null
  apple_url: string | null
  spotify_url: string | null
  youtube_url: string | null
  hosts: PublicEntityRef[]
  recurring: PublicEntityRef[]
}

export interface PublicEpisodeListItem {
  slug: string
  title: string
  pub_date: string | null
  duration_formatted: string | null
  image_url: string | null
  summary_preview: string | null
  guests: PublicEntityRef[]
  podcast?: { slug: string; title: string; image_url: string | null }
}

export interface PublicEpisode {
  slug: string
  title: string
  description: string | null
  pub_date: string | null
  duration_formatted: string | null
  image_url: string | null
  explicit: boolean | null
  episode_number: number | null
  season_number: number | null
  website_url: string | null
  audio_url: string | null
  platform_links: { platform: string; url: string }[]
  has_transcript: boolean
  hosts: PublicEntityRef[]
  guests: PublicEntityRef[]
  podcast: {
    slug: string
    title: string
    author: string | null
    image_url: string | null
    apple_url: string | null
    spotify_url: string | null
    youtube_url: string | null
  }
}

export interface PublicSummary {
  content: string
  language: string
  canonical_language: string
  available_languages: string[]
}

export interface PublicQuote {
  text: string
  start_seconds: number
  speaker: { name: string; slug: string }
  about: { name: string; type: PublicEntityType; slug: string; id: string }
  episode: { slug: string; title: string; pub_date: string | null; image_url: string | null }
  podcast: { slug: string; title: string }
}

export interface PublicEntityPayload {
  entity: { name: string; type: PublicEntityType; slug: string; description: string | null; wikidata_qid: string | null }
  enrichment: {
    headline: string | null
    image_url: string | null
    image_attribution: string | null
    wikipedia_extract: string | null
    wikipedia_url: string | null
    facts: { label: string; value: string; url: string | null }[]
    affiliations: { relation: string; label: string; ref: { type: PublicEntityType; slug: string } | null }[]
  } | null
  stats: { shows: number; episodes: number; since: string | null; last_30_days: number }
  activity: { start: string; weeks: number[] }
  roles: {
    hosts: { slug: string; title: string; count: number | null }[]
    recurring: { slug: string; title: string; count: number | null }[]
    guest_episodes: {
      podcast_slug: string
      podcast_title: string
      episode_slug: string
      episode_title: string
      published_at: string | null
    }[]
  }
  most_discussed_on: { slug: string; title: string; count: number | null }[]
  related: (PublicEntityRef & { episode_count: number })[]
  featured_quote: PublicQuote | null
  topics: (PublicEntityRef & { id: string; quotes: number })[]
  speakers: { name: string; slug: string; quotes: number; shows: number; image_url: string | null; quote: string | null }[]
  quote_counts: { said: number; about: number }
  indexable: boolean
}

export interface PublicQuotePage {
  quotes: PublicQuote[]
  total: number
  page: number
  next_page: number | null
}

export interface PublicEpisodeQuotes {
  quotes: PublicQuote[]
  /** Quotes matching the speaker filter (all of them without one). */
  total: number
  page: number
  next_page: number | null
  linked: PublicQuote | null
  /** Everyone quoted in the episode, most quotes first (ignores the filter). */
  speakers: { name: string; slug: string; quotes: number }[]
  mentioned: (PublicEntityRef & { count: number })[]
}

export interface PublicTopChart {
  region: string
  available_regions: string[]
  available_categories: string[]
  top_podcasts: {
    rank: number
    name: string
    artist: string | null
    image_url: string | null
    category: string | null
    apple_url: string | null
    podcast_slug: string | null
  }[]
}

export interface PublicWhoSaidWhat {
  entity: PublicEntityRef | null
  days: number
  shows: number
  speakers: number
  quotes: PublicQuote[]
}

/** A 404 from the public API: the page does not exist or is not public. */
export class PublicNotFound extends Error {}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/public${path}`, { credentials: 'include' })
  if (response.status === 404) throw new PublicNotFound(path)
  if (!response.ok) throw new Error(`Public API error: ${response.status}`)
  return response.json() as Promise<T>
}

const enc = encodeURIComponent

export const publicApi = {
  podcast: (slug: string) => get<{ podcast: PublicPodcast }>(`/podcasts/${enc(slug)}`),
  podcastEpisodes: (slug: string, offset = 0, limit = 10) =>
    get<{ episodes: PublicEpisodeListItem[]; total: number; next_offset: number | null }>(
      `/podcasts/${enc(slug)}/episodes?limit=${limit}&offset=${offset}`,
    ),
  episode: (podcastSlug: string, episodeSlug: string) =>
    get<{ episode: PublicEpisode }>(`/podcasts/${enc(podcastSlug)}/episodes/${enc(episodeSlug)}`),
  summary: (podcastSlug: string, episodeSlug: string) =>
    get<PublicSummary>(`/podcasts/${enc(podcastSlug)}/episodes/${enc(episodeSlug)}/summary`),
  episodeQuotes: (podcastSlug: string, episodeSlug: string, q?: number | null, speaker?: string | null, page = 1) => {
    const params = new URLSearchParams({ page: String(page) })
    if (q != null) params.set('q', String(q))
    if (speaker) params.set('speaker', speaker)
    return get<PublicEpisodeQuotes>(`/podcasts/${enc(podcastSlug)}/episodes/${enc(episodeSlug)}/quotes?${params.toString()}`)
  },
  whoSaidWhat: () => get<PublicWhoSaidWhat>('/who-said-what'),
  recentSummaries: (limit = 6) => get<{ episodes: PublicEpisodeListItem[] }>(`/recent-summaries?limit=${limit}`),
  topChart: (region?: string, category?: string) => {
    const params = new URLSearchParams()
    if (region) params.set('region', region)
    if (category) params.set('category', category)
    const qs = params.toString()
    return get<PublicTopChart>(`/top-podcasts${qs ? `?${qs}` : ''}`)
  },
  entity: (type: string, slug: string) => get<PublicEntityPayload>(`/entities/${enc(type)}/${enc(slug)}`),
  entityQuotes: (type: string, slug: string, kind: 'said' | 'about', page = 1, about?: string | null) => {
    const params = new URLSearchParams({ kind, page: String(page) })
    if (about) params.set('about', about)
    return get<PublicQuotePage>(`/entities/${enc(type)}/${enc(slug)}/quotes?${params.toString()}`)
  },
}

/** "mm:ss" or "h:mm:ss" for a start second. */
export function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** "11 Oct 2026" in the visitor's locale order. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function entityPath(ref: { type: string; slug: string }): string {
  return `/entities/${ref.type}/${ref.slug}`
}

export function episodePath(podcastSlug: string, episodeSlug: string): string {
  return `/podcasts/${podcastSlug}/episodes/${episodeSlug}`
}
