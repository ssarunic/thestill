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
 * Each row plays inline through the existing PlayerProvider (the
 * spec calls it the "FloatingPlayer" — same thing as MiniPlayer
 * here). Quote rows navigate to the episode page with `?t=<sec>`,
 * which already handles seek-on-load.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useCorpusSearch, useQuickSearch } from '../hooks/useApi'
import type {
  QuickEntityItem,
  SearchResult,
} from '../api/types'
import { parseQuery } from '../utils/searchOperators'

type Tab = 'all' | 'quotes' | 'entities'

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'quotes', label: 'Quotes' },
  { key: 'entities', label: 'Entities' },
]

export default function SearchResults() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialQuery = searchParams.get('q') ?? ''
  const initialTab = (searchParams.get('tab') as Tab) ?? 'all'

  const [query, setQuery] = useState(initialQuery)
  const [tab, setTab] = useState<Tab>(initialTab)

  const parsed = useMemo(() => parseQuery(query), [query])

  // Keep URL in sync so the page is bookmarkable / shareable.
  useEffect(() => {
    const next = new URLSearchParams()
    if (query) next.set('q', query)
    if (tab !== 'all') next.set('tab', tab)
    setSearchParams(next, { replace: true })
  }, [query, tab, setSearchParams])

  const corpusOptions = useMemo(
    () => ({
      mode: 'hybrid' as const,
      limit: 30,
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

  const corpusResults = corpus.data?.results ?? []

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
          {showCorpus && (
            <Section title={tab === 'quotes' ? undefined : 'Quotes'}>
              {corpusResults.length === 0 && !isLoading && <EmptySection label="quotes" query={parsed.text} />}
              <ul className="space-y-3">
                {corpusResults.map((r, i) => (
                  <QuoteResultRow key={`${r.episode_id}-${r.start_ms}-${i}`} result={r} />
                ))}
              </ul>
            </Section>
          )}
          {showEntities && (
            <Section title={tab === 'entities' ? undefined : 'Entities'}>
              {entityGroups.every((g) => g.items.length === 0) && !isLoading && (
                <EmptySection label="entities" query={parsed.text} />
              )}
              <div className="space-y-4">
                {entityGroups.map((group) => (
                  group.items.length === 0 ? null : (
                    <div key={group.type}>
                      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                        {group.label}
                      </h3>
                      <ul className="space-y-1">
                        {group.items.map((item) => (
                          item.kind === 'entity' ? (
                            <EntityResultRow key={item.id} item={item} />
                          ) : null
                        ))}
                      </ul>
                    </div>
                  )
                ))}
              </div>
            </Section>
          )}
          {isLoading && <Loader />}
        </>
      )}
    </div>
  )
}

function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      {title && <h2 className="mb-3 text-lg font-semibold text-gray-900">{title}</h2>}
      {children}
    </section>
  )
}

function QuoteResultRow({ result }: { result: SearchResult }) {
  const navigate = useNavigate()
  const seconds = Math.floor(result.start_ms / 1000)
  const hasSlugs = !!result.podcast_slug && !!result.episode_slug

  const handleOpen = () => {
    if (hasSlugs) {
      navigate(
        `/podcasts/${result.podcast_slug}/episodes/${result.episode_slug}?t=${seconds}`,
      )
    }
  }

  return (
    <li
      className={`rounded-lg border border-gray-200 p-4 ${
        hasSlugs
          ? 'cursor-pointer hover:border-primary-200 hover:bg-primary-50/50'
          : 'cursor-not-allowed opacity-70'
      }`}
      onClick={hasSlugs ? handleOpen : undefined}
      data-testid="search-quote-row"
    >
      <p className="text-sm text-gray-900">"{result.quote}"</p>
      <p className="mt-2 text-xs text-gray-500">
        {result.speaker ? `${result.speaker} · ` : ''}
        {result.episode_title} · {result.podcast_title}
        {' · '}
        {hasSlugs ? (
          <span className="text-primary-600">▶ play at {formatSeconds(seconds)}</span>
        ) : (
          <span className="text-gray-400">deep link unavailable for legacy episode</span>
        )}
      </p>
    </li>
  )
}

function EntityResultRow({ item }: { item: QuickEntityItem }) {
  // Entity pages are Phase 5; until then fall back to the episodes
  // browser filtered by the entity name.
  return (
    <li>
      <Link
        to={`/episodes?search=${encodeURIComponent(item.name)}`}
        className="flex items-center justify-between rounded px-3 py-2 hover:bg-gray-50"
      >
        <div>
          <span className="font-medium text-gray-900">{item.name}</span>
          {item.matched_alias && (
            <span className="ml-2 text-xs text-gray-500">aka {item.matched_alias}</span>
          )}
        </div>
        <span className="text-xs text-gray-500">
          {item.mention_count} mention{item.mention_count === 1 ? '' : 's'}
        </span>
      </Link>
    </li>
  )
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

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}
