import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { EpisodeEntity, EntityType, RelatedEpisode } from '../../api/types'
import { entityHref, entityStyle } from '../../utils/entityColors'
import { useBackgroundLocation } from '../../hooks/useBackgroundLocation'

// Spec #28 §5.2 right rail (desktop ≥ md). "People in this episode",
// "Companies mentioned", "Related episodes". Hosts/guests/recurring
// surfaced first within the People bucket; salience desc within
// each bucket. Affordance #4: the entity name itself deeplinks to the
// first mention timestamp; play-▷ on hover seeks to it.
//
// Related episodes pulls from vector similarity (qmd was the original
// backend; spec §2.10 swapped to sqlite-vec). The backend averages this
// episode's chunk embeddings into a centroid and returns the nearest
// distinct episodes, capped at 5.

// Per-section visible cap. The full bucket is always sent over the
// wire so the spec's "complete index" intent is preserved; we just
// collapse the long tail behind a "Show all (N)" expander so the
// default view stays scannable. 8 is wider than the above-the-fold
// strip's 5 so the rail genuinely adds something rather than echoing
// the strip.
const DEFAULT_VISIBLE_COUNT = 8

export interface EntityRailProps {
  entities: EpisodeEntity[]
  onSeek?: (seconds: number) => void
  onFocusEntity?: (entityId: string) => void
  relatedEpisodes?: RelatedEpisode[]
  relatedLoading?: boolean
}

function formatPubDate(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function formatTimestamp(ms: number): string {
  const total = Math.floor(ms / 1000)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  if (hh > 0) {
    return `${hh}:${mm.toString().padStart(2, '0')}:${ss.toString().padStart(2, '0')}`
  }
  return `${mm}:${ss.toString().padStart(2, '0')}`
}

interface RailRowProps {
  entity: EpisodeEntity
  onSeek?: (seconds: number) => void
  onFocusEntity?: (entityId: string) => void
}

function RailRow({ entity: episodeEntity, onSeek, onFocusEntity }: RailRowProps) {
  const { entity, mention_count, first_mention_ms, speaker_kind } = episodeEntity
  const style = entityStyle(entity.type)
  const seekSeconds = first_mention_ms / 1000
  const isParticipant = speaker_kind !== 'unknown'

  return (
    <li
      className="group flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-gray-50"
      onMouseEnter={() => onFocusEntity?.(entity.id)}
    >
      <span className={`inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full ${style.dot}`} aria-hidden="true" />
      <Link
        to={entityHref(entity.type, entity.id)}
        // Affordance #4 — first-mention deeplink: name links to entity
        // page on click, but option-click (or the explicit play-▷
        // button) seeks. We can't easily distinguish modifier clicks
        // here without overriding default browser behavior; the
        // play-▷ button is the dedicated seek path.
        className="min-w-0 flex-1 truncate font-medium text-gray-800 hover:text-primary-700"
      >
        {entity.canonical_name}
        {isParticipant && (
          <span className="ml-1.5 rounded bg-gray-100 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-gray-600">
            {speaker_kind}
          </span>
        )}
      </Link>
      <span className="flex-shrink-0 text-xs tabular-nums text-gray-500">{mention_count}×</span>
      {onSeek && (
        <button
          type="button"
          onClick={() => onSeek(seekSeconds)}
          title={`Play first mention at ${formatTimestamp(first_mention_ms)}`}
          aria-label={`Play first mention of ${entity.canonical_name} at ${formatTimestamp(first_mention_ms)}`}
          className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-gray-400 opacity-0 hover:bg-white hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 group-hover:opacity-100 group-focus-within:opacity-100"
        >
          <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
      )}
    </li>
  )
}

export default function EntityRail({
  entities,
  onSeek,
  onFocusEntity,
  relatedEpisodes = [],
  relatedLoading = false,
}: EntityRailProps) {
  // Group by type for the section labels. The payload is already
  // sorted host/guest/recurring/unknown then count desc within each
  // bucket, so we just need to bucket by type while preserving order.
  const buckets: Record<EntityType, EpisodeEntity[]> = {
    person: [],
    company: [],
    product: [],
    topic: [],
  }
  for (const e of entities) {
    buckets[e.entity.type].push(e)
  }

  const hasAny = entities.length > 0

  return (
    <aside
      aria-label="Episode entities"
      className="space-y-4 text-sm"
      data-testid="entity-rail"
    >
      {hasAny && buckets.person.length > 0 && (
        <RailSection title="People in this episode" entities={buckets.person} onSeek={onSeek} onFocusEntity={onFocusEntity} />
      )}
      {hasAny && buckets.company.length > 0 && (
        <RailSection title="Companies mentioned" entities={buckets.company} onSeek={onSeek} onFocusEntity={onFocusEntity} />
      )}
      {hasAny && buckets.product.length > 0 && (
        <RailSection title="Products" entities={buckets.product} onSeek={onSeek} onFocusEntity={onFocusEntity} />
      )}
      {hasAny && buckets.topic.length > 0 && (
        <RailSection title="Topics" entities={buckets.topic} onSeek={onSeek} onFocusEntity={onFocusEntity} />
      )}

      {/* Spec §5.2 right rail — "Related episodes pulls from vector
          similarity; cap at 5." Rendered whenever a fetch is in flight
          or returned hits, so the section keeps a stable slot. */}
      <RelatedEpisodesSection episodes={relatedEpisodes} loading={relatedLoading} />

      {!hasAny && relatedEpisodes.length === 0 && !relatedLoading && (
        <p className="px-2 text-xs italic text-gray-400">
          No entities extracted for this episode yet.
        </p>
      )}
    </aside>
  )
}

interface RelatedEpisodesSectionProps {
  episodes: RelatedEpisode[]
  loading: boolean
}

function RelatedEpisodesSection({ episodes, loading }: RelatedEpisodesSectionProps) {
  // Spec #52 — inside the reader overlay, related-episode clicks stay in
  // the overlay: preserve the background location and replace the history
  // entry so a single Esc/back still closes back to the inbox. On the
  // standalone page (no background) this is a plain navigation.
  const backgroundLocation = useBackgroundLocation()
  // Nothing in flight and nothing found — omit the section entirely so
  // we don't render a bare header with no body.
  if (!loading && episodes.length === 0) return null
  return (
    <section>
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Related episodes</h2>
      {loading ? (
        <p className="px-2 text-xs italic text-gray-400">Finding related episodes…</p>
      ) : (
        <ul className="space-y-0.5">
          {episodes.map((ep) => {
            const pubDate = formatPubDate(ep.published_at)
            return (
              <li key={ep.episode_id} className="rounded-md px-2 py-1 text-sm hover:bg-gray-50">
                <Link
                  to={`/podcasts/${ep.podcast_slug}/episodes/${ep.episode_slug}`}
                  state={backgroundLocation ? { backgroundLocation } : undefined}
                  replace={backgroundLocation != null}
                  className="block min-w-0 font-medium text-gray-800 hover:text-primary-700"
                >
                  <span className="block truncate">{ep.episode_title}</span>
                  <span className="block truncate text-xs font-normal text-gray-500">
                    {ep.podcast_title}
                    {pubDate ? ` · ${pubDate}` : ''}
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

interface RailSectionProps {
  title: string
  entities: EpisodeEntity[]
  onSeek?: (seconds: number) => void
  onFocusEntity?: (entityId: string) => void
}

function RailSection({ title, entities, onSeek, onFocusEntity }: RailSectionProps) {
  const [expanded, setExpanded] = useState(false)
  const overflow = entities.length - DEFAULT_VISIBLE_COUNT
  const visible = expanded || overflow <= 0 ? entities : entities.slice(0, DEFAULT_VISIBLE_COUNT)
  return (
    <section>
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</h2>
      <ul className="space-y-0.5">
        {visible.map((e) => (
          <RailRow key={e.entity.id} entity={e} onSeek={onSeek} onFocusEntity={onFocusEntity} />
        ))}
      </ul>
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-1 ml-2 text-xs font-medium text-primary-700 hover:text-primary-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded"
        >
          {expanded ? 'Show fewer' : `Show all (${entities.length})`}
        </button>
      )}
    </section>
  )
}
