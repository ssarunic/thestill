import { useParams, Link } from 'react-router-dom'
import { useEntitySummary } from '../hooks/useApi'
import type { EntityType } from '../api/types'
import { entityHref, entityStyle } from '../utils/entityColors'

// Spec #28 §5.1 — minimal entity page. The full design (timeline
// sparkline, NotableQuotes pull-out, MentionFeed pagination) lands in
// a follow-up; this version satisfies the routing contract so
// hover-card "Go to entity page" links and the command bar's entity
// hits resolve to a real page.

const VALID_TYPES = new Set<EntityType>(['person', 'company', 'product', 'topic'])

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

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export default function Entities() {
  const { entityType, idSlug } = useParams<{ entityType: string; idSlug: string }>()
  const validType = entityType && VALID_TYPES.has(entityType as EntityType)
    ? (entityType as EntityType)
    : null
  const { data, isLoading, error } = useEntitySummary(validType, idSlug ?? null)

  if (!validType || !idSlug) {
    return (
      <div className="text-center py-12">
        <h1 className="text-xl font-semibold text-gray-800">Entity not found</h1>
        <p className="mt-2 text-sm text-gray-500">Unknown entity type or id.</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading entity</h2>
          <p className="text-red-600 text-sm">{error.message}</p>
        </div>
      </div>
    )
  }

  if (isLoading || !data) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600" />
      </div>
    )
  }

  const style = entityStyle(data.entity.type)
  // Tolerate older API responses that don't include the role fields yet
  // — without this guard, `.length` on undefined blanks the whole page.
  const hostsPodcasts = data.hosts_podcasts ?? []
  const recurringPodcasts = data.recurring_podcasts ?? []
  const guestEpisodes = data.guest_episodes ?? []

  return (
    <div className="space-y-6">
      <nav className="text-sm flex items-center gap-1">
        <Link to="/" className="text-gray-500 hover:text-gray-700">Home</Link>
        <span className="text-gray-400">/</span>
        <span className="text-gray-700">Entities</span>
        <span className="text-gray-400">/</span>
        <span className="text-gray-700">{style.label}s</span>
      </nav>

      {/* Header card */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-3">
        <div className="flex items-center gap-3">
          <span className={`inline-block h-3 w-3 rounded-full ${style.dot}`} aria-hidden="true" />
          <span className="text-xs uppercase tracking-wide text-gray-500">{style.label}</span>
          <span className="ml-auto text-sm tabular-nums text-gray-500">
            {hostsPodcasts.length > 0 && (
              <span className="mr-3">
                Host of {hostsPodcasts.length} podcast{hostsPodcasts.length === 1 ? '' : 's'}
              </span>
            )}
            {guestEpisodes.length > 0 && (
              <span className="mr-3">
                Guest on {guestEpisodes.length} episode{guestEpisodes.length === 1 ? '' : 's'}
              </span>
            )}
            {data.mention_count} mention{data.mention_count === 1 ? '' : 's'}
          </span>
        </div>
        <h1 className="text-3xl font-bold text-gray-900">{data.entity.canonical_name}</h1>
        {data.aliases.length > 0 && (
          <div className="text-sm text-gray-500">
            <span className="font-medium text-gray-600">Also known as:</span>{' '}
            {data.aliases.join(', ')}
          </div>
        )}
        {data.description && (
          <p className="text-sm text-gray-700">{data.description}</p>
        )}
        {data.entity.wikidata_qid && (
          <a
            href={`https://www.wikidata.org/wiki/${data.entity.wikidata_qid}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-gray-500 hover:text-gray-700 hover:underline"
          >
            Wikidata: {data.entity.wikidata_qid} ↗
          </a>
        )}
      </div>

      {/* Anchor roles — host of, recurring on, guest on */}
      {(hostsPodcasts.length > 0 || recurringPodcasts.length > 0) && (
        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
            Podcast roles
          </h2>
          {hostsPodcasts.length > 0 && (
            <div className="mt-3">
              <div className="text-xs uppercase tracking-wide text-gray-400">Hosts</div>
              <ul className="mt-1 divide-y divide-gray-100">
                {hostsPodcasts.map((p) => (
                  <li key={`host-${p.podcast_id}`} className="py-2 flex items-baseline gap-2">
                    {p.podcast_slug ? (
                      <Link
                        to={`/podcasts/${p.podcast_slug}`}
                        className="text-sm font-medium text-primary-700 hover:underline"
                      >
                        {p.podcast_title}
                      </Link>
                    ) : (
                      <span className="text-sm font-medium text-gray-800">{p.podcast_title}</span>
                    )}
                    <span className="ml-auto text-xs tabular-nums text-gray-500">
                      {p.episode_count} episode{p.episode_count === 1 ? '' : 's'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {recurringPodcasts.length > 0 && (
            <div className="mt-4">
              <div className="text-xs uppercase tracking-wide text-gray-400">Recurring on</div>
              <ul className="mt-1 divide-y divide-gray-100">
                {recurringPodcasts.map((p) => (
                  <li key={`recurring-${p.podcast_id}`} className="py-2 flex items-baseline gap-2">
                    {p.podcast_slug ? (
                      <Link
                        to={`/podcasts/${p.podcast_slug}`}
                        className="text-sm font-medium text-primary-700 hover:underline"
                      >
                        {p.podcast_title}
                      </Link>
                    ) : (
                      <span className="text-sm font-medium text-gray-800">{p.podcast_title}</span>
                    )}
                    <span className="ml-auto text-xs tabular-nums text-gray-500">
                      {p.episode_count} episode{p.episode_count === 1 ? '' : 's'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {guestEpisodes.length > 0 && (
        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
            Guest appearances
          </h2>
          <ul className="mt-3 divide-y divide-gray-100">
            {guestEpisodes.map((ep) => {
              const epHref = ep.podcast_slug && ep.episode_slug
                ? `/podcasts/${ep.podcast_slug}/episodes/${ep.episode_slug}`
                : null
              return (
                <li key={`guest-${ep.episode_id}`} className="py-3">
                  <div className="flex items-baseline gap-2">
                    {epHref ? (
                      <Link to={epHref} className="text-sm font-medium text-primary-700 hover:underline">
                        {ep.episode_title}
                      </Link>
                    ) : (
                      <span className="text-sm font-medium text-gray-800">{ep.episode_title}</span>
                    )}
                    <span className="ml-auto text-xs text-gray-500">{formatDate(ep.published_at)}</span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">{ep.podcast_title}</div>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {/* Co-occurring entities */}
      {data.cooccurring.length > 0 && (
        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
            Frequently appears with
          </h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {data.cooccurring.map((row) => {
              const otherStyle = entityStyle(row.entity.type)
              return (
                <Link
                  key={row.entity.id}
                  to={entityHref(row.entity.type, row.entity.id)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium hover:brightness-95 ${otherStyle.pillBg} ${otherStyle.pillText} ${otherStyle.pillBorder}`}
                >
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${otherStyle.dot}`} aria-hidden="true" />
                  {row.entity.canonical_name}
                  <span className="opacity-70">{row.episode_count} ep</span>
                </Link>
              )
            })}
          </div>
        </section>
      )}

      {/* Recent mentions */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
          Recent mentions
        </h2>
        {data.recent_mentions.length === 0 ? (
          <p className="mt-3 text-sm italic text-gray-400">
            No transcript mentions yet.
            {hostsPodcasts.length > 0 && ' Hosts often go unnamed in their own show.'}
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-gray-100">
            {data.recent_mentions.map((row, idx) => {
              const seekHref = row.podcast_slug && row.episode_slug
                ? `/podcasts/${row.podcast_slug}/episodes/${row.episode_slug}?t=${Math.floor(row.start_ms / 1000)}`
                : null
              return (
                <li key={`${row.episode_id}-${row.start_ms}-${idx}`} className="py-3">
                  <div className="flex items-baseline gap-2 text-xs text-gray-500">
                    {seekHref ? (
                      <Link to={seekHref} className="font-mono tabular-nums text-primary-700 hover:underline">
                        {formatTimestamp(row.start_ms)}
                      </Link>
                    ) : (
                      <span className="font-mono tabular-nums">{formatTimestamp(row.start_ms)}</span>
                    )}
                    <span>·</span>
                    {row.speaker && <span>{row.speaker}</span>}
                    <span className="ml-auto">{formatDate(row.published_at)}</span>
                  </div>
                  <p className="mt-1 text-sm text-gray-800">"{row.quote}"</p>
                  <div className="mt-1 text-xs text-gray-500">
                    <span className="font-medium text-gray-700">{row.episode_title}</span> · {row.podcast_title}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
