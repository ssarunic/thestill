/**
 * Spec #28 §4.1 — ⌘K command bar.
 *
 * Modal overlay with a single input and grouped typeahead. Lexical
 * search only (Strategy §2 — never silently upgrade for the typing
 * path). Operators are parsed client-side via parseQuery and flow
 * into the request as structured query params.
 *
 * Keyboard:
 *   ⌘K / Ctrl+K     toggle open
 *   Esc             close
 *   ↑ / ↓           move selection (flat across groups)
 *   Enter           activate selected item
 *   Cmd/Ctrl+Enter  jump to /search results page
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuickSearch } from '../hooks/useApi'
import type {
  QuickEntityItem,
  QuickEpisodeItem,
  QuickGroup,
  QuickQuoteItem,
  QuickSearchItem,
  QuickSearchOptions,
} from '../api/types'
import { parseQuery } from '../utils/searchOperators'
import type { ParsedFilters } from '../utils/searchOperators'

interface CommandBarProps {
  isOpen: boolean
  onClose: () => void
}

interface FlatItem {
  group: QuickGroup
  item: QuickSearchItem
  /** Position in the flat list across groups, used for keyboard nav. */
  flatIndex: number
}

export default function CommandBar({ isOpen, onClose }: CommandBarProps) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const navigate = useNavigate()

  const parsed = useMemo(() => parseQuery(query), [query])
  const searchOptions = useMemo(
    () => buildSearchOptions(parsed.filters),
    [parsed.filters],
  )
  const { data, isFetching, isError, error } = useQuickSearch(parsed.text, searchOptions)

  // Reset selection whenever the query changes — selecting the first
  // hit by default is the expected typeahead UX.
  useEffect(() => {
    setSelected(0)
  }, [query, data])

  // Focus + clear when the modal opens; clear state on close so the
  // next open starts fresh.
  useEffect(() => {
    if (isOpen) {
      const id = window.setTimeout(() => inputRef.current?.focus(), 0)
      return () => window.clearTimeout(id)
    }
    setQuery('')
    setSelected(0)
    return undefined
  }, [isOpen])

  // Build a flat list of items so ↑/↓ can walk across groups.
  const flat: FlatItem[] = useMemo(() => {
    if (!data) return []
    const out: FlatItem[] = []
    let idx = 0
    for (const group of data.groups) {
      for (const item of group.items) {
        out.push({ group, item, flatIndex: idx++ })
      }
    }
    return out
  }, [data])

  const activate = useCallback(
    (entry: FlatItem) => {
      onClose()
      handleActivate(entry.item, navigate)
    },
    [navigate, onClose],
  )

  const handleKey = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setSelected((s) => Math.min(s + 1, Math.max(0, flat.length - 1)))
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setSelected((s) => Math.max(0, s - 1))
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        if (event.metaKey || event.ctrlKey) {
          // Cmd+Enter — jump to /search regardless of selection.
          onClose()
          if (parsed.text) navigate(`/search?q=${encodeURIComponent(parsed.text)}`)
          return
        }
        const entry = flat[selected]
        if (entry) activate(entry)
        else if (parsed.text) {
          onClose()
          navigate(`/search?q=${encodeURIComponent(parsed.text)}`)
        }
      }
    },
    [activate, flat, navigate, onClose, parsed.text, selected],
  )

  // Scroll selected row into view as ↓ moves past the visible window.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-flat-index="${selected}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  if (!isOpen) return null

  const totalHits = flat.length
  const trimmed = parsed.text.trim()
  const hasFilters = hasActiveFilters(parsed.filters)
  const showIdle = trimmed.length < 2
  const showEmpty = !showIdle && !isFetching && totalHits === 0 && !!data
  const showError = !showIdle && isError

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[10vh] sm:pt-[15vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Search"
    >
      <div className="w-full max-w-2xl rounded-xl bg-white shadow-2xl ring-1 ring-black/5 overflow-hidden">
        <div className="flex items-center gap-3 border-b border-gray-200 px-4">
          <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Search episodes, people, companies, topics, quotes…"
            className="flex-1 py-4 text-base outline-none placeholder:text-gray-400"
            aria-label="Search query"
            data-testid="cmdk-input"
          />
          {isFetching && <Spinner />}
          <kbd className="hidden sm:inline-flex items-center rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs text-gray-500">
            Esc
          </kbd>
        </div>

        <div ref={listRef} className="max-h-[60vh] overflow-y-auto">
          {showIdle && <IdleHint hasFilters={hasFilters} filters={parsed.filters} />}
          {parsed.hints.length > 0 && <HintBanner hints={parsed.hints} />}
          {showError && <ErrorState message={(error as Error)?.message ?? 'Search is offline'} />}
          {showEmpty && <EmptyState query={trimmed} />}

          {!showIdle && data && totalHits > 0 && data.groups.map((group) => {
            if (group.items.length === 0) return null
            return (
              <Group
                key={group.type}
                group={group}
                selectedFlatIndex={selected}
                onActivate={activate}
                lookup={flat}
              />
            )
          })}
        </div>

        <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-4 py-2 text-xs text-gray-500">
          <div className="flex items-center gap-3">
            <span>↑↓ navigate</span>
            <span>↵ select</span>
            <span className="hidden sm:inline">⌘↵ all results</span>
          </div>
          {totalHits > 0 && data && (
            <button
              type="button"
              onClick={() => {
                onClose()
                navigate(data.see_all_url)
              }}
              className="font-medium text-primary-700 hover:underline"
            >
              See all results →
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Group({
  group,
  selectedFlatIndex,
  onActivate,
  lookup,
}: {
  group: QuickGroup
  selectedFlatIndex: number
  onActivate: (entry: FlatItem) => void
  lookup: FlatItem[]
}) {
  return (
    <section className="py-2">
      <div className="px-4 py-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
        {group.label}
      </div>
      <ul role="listbox">
        {group.items.map((item) => {
          const flatEntry = lookup.find((f) => f.item === item)
          if (!flatEntry) return null
          const isSelected = flatEntry.flatIndex === selectedFlatIndex
          return (
            <li
              key={itemKey(item)}
              data-flat-index={flatEntry.flatIndex}
              role="option"
              aria-selected={isSelected}
              onClick={() => onActivate(flatEntry)}
              className={`cursor-pointer px-4 py-2 text-sm ${
                isSelected ? 'bg-primary-50 text-primary-900' : 'hover:bg-gray-50'
              }`}
              data-testid={`cmdk-item-${item.kind}`}
            >
              <ItemRow item={item} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function ItemRow({ item }: { item: QuickSearchItem }) {
  if (item.kind === 'episode') return <EpisodeRow item={item} />
  if (item.kind === 'entity') return <EntityRow item={item} />
  return <QuoteRow item={item} />
}

function EpisodeRow({ item }: { item: QuickEpisodeItem }) {
  return (
    <div className="flex items-center gap-3">
      {item.image_url ? (
        <img src={item.image_url} alt="" className="h-8 w-8 flex-shrink-0 rounded object-cover" />
      ) : (
        <div className="h-8 w-8 flex-shrink-0 rounded bg-gray-200" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{item.title}</div>
        <div className="truncate text-xs text-gray-500">
          {item.podcast_title}
          {item.pub_date ? ` · ${formatDate(item.pub_date)}` : ''}
        </div>
      </div>
    </div>
  )
}

function EntityRow({ item }: { item: QuickEntityItem }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded bg-gray-100 text-xs font-semibold text-gray-600">
        {entityBadge(item.entity_type)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 truncate font-medium">
          <span className="truncate">{item.name}</span>
          {item.role && (
            <span
              className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${roleBadgeClasses(item.role)}`}
            >
              {item.role}
            </span>
          )}
          {item.matched_alias && (
            <span className="ml-1 text-xs font-normal text-gray-500">aka {item.matched_alias}</span>
          )}
        </div>
        <div className="truncate text-xs text-gray-500">
          {entityRowSummary(item)}
        </div>
      </div>
    </div>
  )
}

function roleBadgeClasses(role: 'guest' | 'host' | 'recurring'): string {
  if (role === 'guest') return 'bg-emerald-100 text-emerald-800 ring-emerald-200'
  if (role === 'host') return 'bg-blue-100 text-blue-800 ring-blue-200'
  return 'bg-violet-100 text-violet-800 ring-violet-200'
}

function entityRowSummary(item: QuickEntityItem): string {
  if (item.role && item.role_episode_count > 0) {
    const word = item.role === 'guest' ? 'on' : 'across'
    return `${word} ${item.role_episode_count} episode${item.role_episode_count === 1 ? '' : 's'}`
  }
  return `${item.mention_count} mention${item.mention_count === 1 ? '' : 's'}`
}

function QuoteRow({ item }: { item: QuickQuoteItem }) {
  return (
    <div>
      <div className="line-clamp-2 text-gray-900">"{item.quote}"</div>
      <div className="mt-0.5 truncate text-xs text-gray-500">
        {item.speaker ? `${item.speaker} · ` : ''}
        {item.episode_title} · {item.podcast_title}
      </div>
    </div>
  )
}

function IdleHint({
  hasFilters,
  filters,
}: {
  hasFilters: boolean
  filters: ParsedFilters
}) {
  if (hasFilters) {
    return (
      <div className="px-4 py-8 text-center text-sm text-gray-500" data-testid="cmdk-idle-with-filters">
        <p className="mb-2">Add a search term to narrow these filters.</p>
        <div className="flex flex-wrap justify-center gap-1.5 text-xs">
          {summariseFilters(filters).map((label) => (
            <span key={label} className="rounded bg-primary-50 px-2 py-0.5 text-primary-800">
              {label}
            </span>
          ))}
        </div>
      </div>
    )
  }
  return (
    <div className="px-4 py-8 text-center text-sm text-gray-500">
      <p className="mb-2">Type to search.</p>
      <p className="text-xs">
        Try <code className="rounded bg-gray-100 px-1 py-0.5">person:elon-musk</code>,{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5">company:tesla</code>, or{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5">after:2025-01-01</code>.
      </p>
    </div>
  )
}

function hasActiveFilters(filters: ParsedFilters): boolean {
  return (
    !!filters.date_from ||
    !!filters.date_to ||
    !!filters.podcast_slug ||
    (filters.person?.length ?? 0) > 0 ||
    (filters.company?.length ?? 0) > 0 ||
    (filters.topic?.length ?? 0) > 0
  )
}

function summariseFilters(filters: ParsedFilters): string[] {
  const labels: string[] = []
  for (const v of filters.person ?? []) labels.push(`person:${v}`)
  for (const v of filters.company ?? []) labels.push(`company:${v}`)
  for (const v of filters.topic ?? []) labels.push(`topic:${v}`)
  if (filters.podcast_slug) labels.push(`podcast:${filters.podcast_slug}`)
  if (filters.date_from) labels.push(`after:${filters.date_from}`)
  if (filters.date_to) labels.push(`before:${filters.date_to}`)
  return labels
}

// Convert the parsed operator filters to the wire shape /api/search/quick
// expects. ``person:elon-musk`` becomes ``has_entity=person:elon-musk``
// — entity ids are ``"{type}:{slug}"`` so we reconstruct by joining.
// Values that already look like a fully-qualified id are passed through
// unchanged so e.g. ``person:person:elon-musk`` doesn't double-prefix.
function buildSearchOptions(filters: ParsedFilters): QuickSearchOptions {
  const has_entity: string[] = []
  for (const v of filters.person ?? []) has_entity.push(qualifyEntityId('person', v))
  for (const v of filters.company ?? []) has_entity.push(qualifyEntityId('company', v))
  for (const v of filters.topic ?? []) has_entity.push(qualifyEntityId('topic', v))
  const opts: QuickSearchOptions = {}
  if (has_entity.length > 0) opts.has_entity = has_entity
  if (filters.podcast_slug) opts.podcast_slug = filters.podcast_slug
  if (filters.date_from) opts.date_from = filters.date_from
  if (filters.date_to) opts.date_to = filters.date_to
  return opts
}

function qualifyEntityId(type: 'person' | 'company' | 'topic', value: string): string {
  return value.includes(':') ? value : `${type}:${value}`
}

function HintBanner({ hints }: { hints: { operator: string; value: string; reason: string }[] }) {
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
      {hints.map((h, i) => (
        <div key={i}>
          <code>{h.operator}:{h.value}</code> — {h.reason}
        </div>
      ))}
    </div>
  )
}

function EmptyState({ query }: { query: string }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-gray-500" data-testid="cmdk-empty">
      No matches for <span className="font-medium text-gray-700">"{query}"</span>.
      <div className="mt-1 text-xs">Try a different phrase or remove operators.</div>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-red-600" data-testid="cmdk-error">
      Search is offline.
      <div className="mt-1 text-xs text-red-500">{message}</div>
    </div>
  )
}

function Spinner() {
  return (
    <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-primary-600" />
  )
}

function entityBadge(type: QuickEntityItem['entity_type']): string {
  switch (type) {
    case 'person':
      return 'P'
    case 'company':
      return 'C'
    case 'product':
      return 'PR'
    case 'topic':
      return 'T'
  }
}

function itemKey(item: QuickSearchItem): string {
  if (item.kind === 'episode') return `e:${item.episode_id}`
  if (item.kind === 'entity') return `n:${item.id}`
  return `q:${item.episode_id}:${item.start_ms}`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function handleActivate(item: QuickSearchItem, navigate: ReturnType<typeof useNavigate>) {
  if (item.kind === 'episode') {
    navigate(`/podcasts/${item.podcast_slug}/episodes/${item.episode_slug}`)
    return
  }
  if (item.kind === 'quote') {
    const seconds = Math.floor(item.start_ms / 1000)
    navigate(`/podcasts/${item.podcast_slug}/episodes/${item.episode_slug}?t=${seconds}`)
    return
  }
  // Entity hits — Phase 5.1 entity page is now live. The entity id is
  // ``"{type}:{slug}"`` so we strip the prefix for the URL path
  // (entityHref builds the canonical /entities/<type>/<slug> shape).
  const colon = item.id.indexOf(':')
  const slug = colon === -1 ? item.id : item.id.slice(colon + 1)
  navigate(`/entities/${item.entity_type}/${slug}`)
}
