/**
 * Spec #28 §4.2 — full search results page.
 *
 * The "see all results" escape hatch from the ⌘K command bar. Three
 * tabs: All / Quotes / Entities.
 *
 * - All / Quotes hit /api/search/corpus (mode=hybrid) so we get
 *   semantic recall, not just BM25. Typing latency isn't on the
 *   critical path here — the typeahead in the command bar is.
 * - Entities tab hits /api/search/quick with limit_per_group=10
 *   and renders only the entity groups.
 *
 * Quotes are grouped by episode: one card per episode with its best
 * moments (`per_episode` caps them server-side, so an episode that says
 * the term thirty times can't flood the page or push the others out of
 * the result set) and "Show all N mentions", which loads that episode's
 * literal matches in time order.
 *
 * Each moment plays inline through the existing PlayerProvider (the
 * spec calls it the "FloatingPlayer" — same thing as MiniPlayer
 * here). Without an audio URL a moment navigates to the episode page
 * with `?t=<sec>`, which already handles seek-on-load.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useCorpusSearch, useQuickSearch } from '../hooks/useApi'
import type {
  CorpusSearchOptions,
  EntityType,
  QuickEntityItem,
  SearchResult,
} from '../api/types'
import SmartImage from '../components/SmartImage'
import { formatEyebrowDate } from '../utils/episodeFormat'
import { formatClock } from '../utils/formatClock'
import { parseQuery } from '../utils/searchOperators'
import { useDebouncedSearchParam } from '../hooks/useDebouncedSearchParam'
import { entityHref, entityStyle } from '../utils/entityColors'
import { usePlayer } from '../contexts/PlayerContext'

type Tab = 'all' | 'quotes' | 'entities'

// Moments shown per episode card before "Show all N mentions". With the
// 50-hit limit this leaves room for at least 25 episodes.
const MOMENTS_PER_EPISODE = 2
// An expanded card loads at most this many of the episode's matches.
const EPISODE_MOMENTS_LIMIT = 50

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'quotes', label: 'Quotes' },
  { key: 'entities', label: 'Entities' },
]

export default function SearchResults() {
  // Restore scroll position on Back from a result's detail page.
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = (searchParams.get('tab') as Tab) ?? 'all'

  // ``query`` is what the user is typing; ``settledQuery`` (from ?q=) is what
  // the searches key on. Debounced so typing "legora" runs one search, not
  // six — each hybrid search embeds, and with spec #89 reranks, on the server.
  const { value: query, debouncedValue: settledQuery, setValue: setQuery } = useDebouncedSearchParam('q', 300)
  const [tab, setTab] = useState<Tab>(initialTab)

  const parsed = useMemo(() => parseQuery(settledQuery), [settledQuery])

  // Keep ?tab= in sync; ?q= is owned by useDebouncedSearchParam.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (tab !== 'all') next.set('tab', tab)
        else next.delete('tab')
        return next
      },
      { replace: true },
    )
  }, [tab, setSearchParams])

  const corpusOptions = useMemo(
    () => ({
      mode: 'hybrid' as const,
      limit: 50,
      per_episode: MOMENTS_PER_EPISODE,
      date_from: parsed.filters.date_from,
      date_to: parsed.filters.date_to,
    }),
    [parsed.filters.date_from, parsed.filters.date_to],
  )
  const quickOptions = useMemo(
    () => ({
      limit_per_group: 10,
      date_from: parsed.filters.date_from,
    }),
    [parsed.filters.date_from],
  )

  const showCorpus = tab === 'all' || tab === 'quotes'
  const showEntities = tab === 'all' || tab === 'entities'

  const corpus = useCorpusSearch(showCorpus ? parsed.text : '', corpusOptions)
  const quick = useQuickSearch(showEntities ? parsed.text : '', quickOptions)

  const idle = parsed.text.trim().length < 2
  const isLoading = (showCorpus && corpus.isFetching) || (showEntities && quick.isFetching)
  const isError = (showCorpus && corpus.isError) || (showEntities && quick.isError)

  const entityGroups = useMemo(() => {
    if (!quick.data) return []
    return quick.data.groups.filter((g) => g.type === 'person' || g.type === 'company' || g.type === 'topic')
  }, [quick.data])

  const episodeGroups = useMemo(() => groupByEpisode(corpus.data?.results ?? []), [corpus.data])
  const matchCounts = corpus.data?.match_counts ?? {}

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Search</h1>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the corpus…"
          className="mt-3 w-full rounded-lg border border-gray-300 px-4 py-3 text-base outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          autoFocus
          data-testid="search-page-input"
        />
        {parsed.hints.length > 0 && (
          <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {parsed.hints.map((h, i) => (
              <div key={i}>
                <code>{h.operator}:{h.value}</code> — {h.reason}
              </div>
            ))}
          </div>
        )}
      </div>

      <nav className="mb-4 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? 'border-b-2 border-primary-600 text-primary-700'
                : 'text-gray-500 hover:text-gray-900'
            }`}
            data-testid={`search-tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {idle && <IdleState />}
      {!idle && isError && <ErrorState message={(corpus.error || quick.error)?.toString() ?? 'Search is offline'} />}
      {!idle && !isError && (
        <>
          {showEntities && (
            <Section title={tab === 'entities' ? undefined : 'Top matches'}>
              {entityGroups.every((g) => g.items.length === 0) && !isLoading && (
                <EmptySection label="entities" query={parsed.text} />
              )}
              <div className="space-y-4">
                {entityGroups.map((group) => (
                  group.items.length === 0 ? null : (
                    <div key={group.type}>
                      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">
                        {group.label}
                      </h3>
                      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {group.items.map((item) => (
                          item.kind === 'entity' ? (
                            <EntityResultCard key={item.id} item={item} />
                          ) : null
                        ))}
                      </ul>
                    </div>
                  )
                ))}
              </div>
            </Section>
          )}
          {showCorpus && (
            <Section
              title={tab === 'quotes' ? undefined : 'Quotes'}
              aside={episodeGroups.length > 0 ? pluralize(episodeGroups.length, 'episode') : undefined}
            >
              {episodeGroups.length === 0 && !isLoading && <EmptySection label="quotes" query={parsed.text} />}
              <ul className="space-y-3">
                {episodeGroups.map((moments) => (
                  <EpisodeResultCard
                    key={moments[0].episode_id}
                    moments={moments}
                    matchCount={matchCounts[moments[0].episode_id] ?? 0}
                    query={parsed.text}
                    dateFrom={parsed.filters.date_from}
                    dateTo={parsed.filters.date_to}
                  />
                ))}
              </ul>
            </Section>
          )}
          {isLoading && <Loader />}
        </>
      )}
    </div>
  )
}

function Section({ title, aside, children }: { title?: string; aside?: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      {title && (
        <h2 className="mb-3 flex items-baseline gap-2 text-lg font-semibold text-gray-900">
          {title}
          {aside && <span className="text-sm font-normal text-gray-500">{aside}</span>}
        </h2>
      )}
      {!title && aside && <p className="mb-3 text-sm text-gray-500">{aside}</p>}
      {children}
    </section>
  )
}

/** Hits grouped by episode, episodes in the order of their best hit. */
function groupByEpisode(results: SearchResult[]): SearchResult[][] {
  const groups = new Map<string, SearchResult[]>()
  for (const r of results) {
    const group = groups.get(r.episode_id)
    if (group) group.push(r)
    else groups.set(r.episode_id, [r])
  }
  return [...groups.values()]
}

/** The quote without the "Speaker: " prefix the chunk writer indexes it with. */
function quoteBody(result: SearchResult): string {
  const prefix = result.speaker ? `${result.speaker}: ` : null
  return prefix && result.quote.startsWith(prefix) ? result.quote.slice(prefix.length) : result.quote
}

function pluralize(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

function EpisodeResultCard({
  moments,
  matchCount,
  query,
  dateFrom,
  dateTo,
}: {
  moments: SearchResult[]
  matchCount: number
  query: string
  dateFrom?: string
  dateTo?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const lead = moments[0]
  const episodeHref =
    lead.podcast_slug && lead.episode_slug ? `/podcasts/${lead.podcast_slug}/episodes/${lead.episode_slug}` : null
  const date = formatEyebrowDate(lead.published_at)

  // "All mentions" is the literal matches — the same set match_count
  // counts — in the order they were said.
  const allOptions = useMemo<CorpusSearchOptions>(
    () => ({
      mode: 'lexical',
      limit: EPISODE_MOMENTS_LIMIT,
      episode_id: lead.episode_id,
      date_from: dateFrom,
      date_to: dateTo,
    }),
    [lead.episode_id, dateFrom, dateTo],
  )
  const all = useCorpusSearch(expanded ? query : '', allOptions)
  const allMoments = useMemo(
    () => (all.data ? [...all.data.results].sort((a, b) => a.start_ms - b.start_ms) : null),
    [all.data],
  )
  const shown = expanded && allMoments ? allMoments : moments
  const canExpand = matchCount > moments.length

  return (
    <li className="rounded-lg border border-gray-200 p-4" data-testid="search-episode-group">
      <div className="flex items-start gap-3">
        <SmartImage
          sources={[lead.image_url]}
          alt=""
          width={40}
          height={40}
          loading="lazy"
          className="h-10 w-10 flex-shrink-0 rounded object-cover"
          fallback={<div className="h-10 w-10 flex-shrink-0 rounded bg-gradient-to-br from-primary-100 to-secondary-100" />}
        />
        <div className="min-w-0 flex-1">
          {episodeHref ? (
            <Link to={episodeHref} className="line-clamp-2 font-semibold text-gray-900 hover:text-primary-700">
              {lead.episode_title}
            </Link>
          ) : (
            <span className="line-clamp-2 font-semibold text-gray-900">{lead.episode_title}</span>
          )}
          <p className="mt-0.5 text-xs text-gray-500">
            {lead.podcast_title}
            {date && ` · ${date}`}
            {matchCount > 0 && (
              <span data-testid="search-episode-match-count"> · {pluralize(matchCount, 'mention')}</span>
            )}
          </p>
        </div>
      </div>
      <ul className="mt-3 space-y-1">
        {shown.map((m, i) => (
          <MomentRow key={`${m.start_ms}-${i}`} result={m} />
        ))}
      </ul>
      {expanded && all.isFetching && !allMoments && <p className="mt-2 text-xs text-gray-500">Loading mentions…</p>}
      {expanded && allMoments && matchCount > allMoments.length && (
        <p className="mt-2 text-xs text-gray-500">
          Showing the {allMoments.length} best matches of {matchCount}, in episode order.
        </p>
      )}
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 text-sm font-medium text-primary-600 hover:text-primary-800"
          data-testid="search-episode-expand"
        >
          {expanded ? 'Show fewer' : `Show all ${pluralize(matchCount, 'mention')}`}
        </button>
      )}
    </li>
  )
}

function MomentRow({ result }: { result: SearchResult }) {
  const navigate = useNavigate()
  const player = usePlayer()
  const seconds = Math.floor(result.start_ms / 1000)
  const hasSlugs = !!result.podcast_slug && !!result.episode_slug
  const canPlayInline = hasSlugs && !!result.audio_url

  // Spec #28 §4.2 — clicking a moment plays it inline through the
  // FloatingPlayer when we have the audio URL on hand; only fall back
  // to a full navigation when the API response is missing audio_url
  // (older episodes or pre-audio_url backend versions). The fallback
  // keeps legacy behaviour intact so we never strand a user on a
  // search result they can't open.
  const handleOpen = () => {
    if (!hasSlugs) return
    if (canPlayInline && result.audio_url) {
      if (player.isCurrent(result.episode_id)) {
        player.seek(seconds)
        if (!player.isPlaying) player.resume()
        return
      }
      player.play(
        {
          episodeId: result.episode_id,
          podcastSlug: result.podcast_slug!,
          episodeSlug: result.episode_slug!,
          title: result.episode_title,
          podcastTitle: result.podcast_title,
          audioUrl: result.audio_url,
          artworkUrl: result.image_url ?? null,
          durationHint: result.duration ?? null,
        },
        { startAt: seconds },
      )
      return
    }
    navigate(
      `/podcasts/${result.podcast_slug}/episodes/${result.episode_slug}?t=${seconds}`,
    )
  }

  return (
    <li
      className={`-mx-2 flex gap-3 rounded-md px-2 py-1.5 ${
        hasSlugs ? 'cursor-pointer hover:bg-primary-50/60' : 'cursor-not-allowed opacity-70'
      }`}
      onClick={hasSlugs ? handleOpen : undefined}
      data-testid="search-quote-row"
    >
      <span
        className={`w-14 flex-shrink-0 pt-0.5 text-right text-xs tabular-nums ${
          hasSlugs ? 'text-primary-600' : 'text-gray-400'
        }`}
        title={hasSlugs ? `Play at ${formatClock(seconds)}` : 'Deep link unavailable for legacy episode'}
      >
        {hasSlugs ? '▶ ' : ''}
        {formatClock(seconds)}
      </span>
      <p className="min-w-0 flex-1 text-sm text-gray-900">
        {result.speaker && <span className="font-medium text-gray-600">{result.speaker}: </span>}"{quoteBody(result)}"
      </p>
    </li>
  )
}

function EntityResultCard({ item }: { item: QuickEntityItem }) {
  const type = item.entity_type as EntityType
  const style = entityStyle(type)
  const roleSummary = formatRoleSummary(item)
  return (
    <li>
      <Link
        to={entityHref(type, item.id)}
        data-testid={`search-entity-card-${item.entity_type}`}
        className={`flex items-center gap-3 rounded-lg border ${style.pillBorder} ${style.pillBg} px-4 py-3 transition hover:shadow-sm`}
      >
        <div
          className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md ${style.pillBg} ${style.pillText} text-sm font-bold ring-1 ring-inset ${style.pillBorder}`}
        >
          {style.shortCode}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="truncate text-row sm:text-base font-semibold text-gray-900">{item.name}</span>
            {item.role && <RoleBadge role={item.role} />}
            {item.matched_alias && (
              <span className="text-xs font-normal text-gray-500">aka {item.matched_alias}</span>
            )}
          </div>
          <div className="text-xs text-gray-600">{roleSummary}</div>
        </div>
        <span className={`text-xs font-medium ${style.pillText}`}>View →</span>
      </Link>
    </li>
  )
}

function RoleBadge({ role }: { role: 'guest' | 'host' | 'recurring' }) {
  const palette =
    role === 'guest'
      ? 'bg-emerald-100 text-emerald-800 ring-emerald-200'
      : role === 'host'
        ? 'bg-blue-100 text-blue-800 ring-blue-200'
        : 'bg-violet-100 text-violet-800 ring-violet-200'
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${palette}`}>
      {role}
    </span>
  )
}

function formatRoleSummary(item: QuickEntityItem): string {
  const style = entityStyle(item.entity_type as EntityType)
  if (item.role && item.role_episode_count > 0) {
    const noun = item.role === 'host' ? 'episode' : 'episode'
    const word = item.role === 'guest' ? 'on' : 'across'
    return `${capitalize(item.role)} ${word} ${item.role_episode_count} ${noun}${item.role_episode_count === 1 ? '' : 's'}`
  }
  if (item.mention_count > 0) {
    return `${style.label} · ${item.mention_count} mention${item.mention_count === 1 ? '' : 's'}`
  }
  return style.label
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function IdleState() {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-white px-6 py-12 text-center text-sm text-gray-500">
      <p className="mb-2">Type at least two characters to search.</p>
      <p className="text-xs">
        Operators: <code>person:</code>, <code>company:</code>, <code>topic:</code>,{' '}
        <code>after:YYYY-MM-DD</code>, <code>before:YYYY-MM-DD</code>.
      </p>
    </div>
  )
}

function EmptySection({ label, query }: { label: string; query: string }) {
  return (
    <div className="rounded border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
      No {label} for "<span className="font-medium text-gray-700">{query}</span>".
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700">
      Search is offline.
      <div className="mt-1 text-xs text-red-600">{message}</div>
    </div>
  )
}

function Loader() {
  return (
    <div className="flex items-center justify-center py-6">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-primary-600" />
    </div>
  )
}
