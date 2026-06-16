import type { ReactNode } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useEntitySummary } from '../hooks/useApi'
import type { EntityCitationRow, EntityType, HostedPodcastRef } from '../api/types'
import { entityHref, entityStyle } from '../utils/entityColors'
import { usePlayer } from '../contexts/PlayerContext'

// Spec #45 Tier 0 — entity page enriched with Wikidata/Wikipedia data:
// hero photo/logo + headline, a vital-stats sidebar, a Wikipedia "About"
// blurb, founder/CEO cross-links, and "most discussed on". Everything
// driven by the optional `enrichment` field degrades gracefully: an
// un-enriched entity (no QID, or not yet fetched) renders the base
// layout it always had.

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

// Bold every case-insensitive occurrence of the mention's `surface_form`
// inside its quote. We don't store character offsets, so — like the
// transcript inline highlighter (applyHighlights.tsx) — we locate the
// surface form by substring search. `surface_form` is the literal text as
// it appeared in this quote (e.g. an alias), so it matches even when the
// quote doesn't use the canonical name. Returns the plain string when the
// surface form is empty or absent (e.g. a coref/pronoun mention).
function boldSurfaceForm(quote: string, surfaceForm: string): ReactNode {
  const needle = surfaceForm.trim().toLowerCase()
  if (!needle) return quote
  const hay = quote.toLowerCase()
  let idx = hay.indexOf(needle)
  if (idx === -1) return quote
  const out: ReactNode[] = []
  let cursor = 0
  let key = 0
  while (idx !== -1) {
    if (idx > cursor) out.push(quote.slice(cursor, idx))
    out.push(
      <strong key={key++} className="font-semibold text-gray-900">
        {quote.slice(idx, idx + needle.length)}
      </strong>,
    )
    cursor = idx + needle.length
    idx = hay.indexOf(needle, cursor)
  }
  if (cursor < quote.length) out.push(quote.slice(cursor))
  return <>{out}</>
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

// Shared row list for the "Hosts" and "Recurring on" sub-sections —
// identical markup, only the label and key prefix differ.
function PodcastRoleList({ podcasts, keyPrefix }: { podcasts: HostedPodcastRef[]; keyPrefix: string }) {
  return (
    <ul className="mt-1 divide-y divide-gray-100">
      {podcasts.map((p) => (
        <li key={`${keyPrefix}-${p.podcast_id}`} className="py-2 flex items-baseline gap-2">
          {p.podcast_slug ? (
            <Link to={`/podcasts/${p.podcast_slug}`} className="text-sm font-medium text-primary-700 hover:underline">
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
  )
}

export default function Entities() {
  const { entityType, idSlug } = useParams<{ entityType: string; idSlug: string }>()
  const validType = entityType && VALID_TYPES.has(entityType as EntityType)
    ? (entityType as EntityType)
    : null
  const { data, isLoading, error } = useEntitySummary(validType, idSlug ?? null)
  const player = usePlayer()

  // Spec #28 §5.1 — clicking a quote timestamp on the entity page must
  // open the FloatingPlayer at the right moment, not navigate away to
  // the episode detail page (which loses the user's place). We hand
  // the audio URL + start offset directly to ``player.play`` so the
  // MiniPlayer takes over inline. Falls back to a deep-link Link when
  // ``audio_url`` is missing (older API responses, episode without
  // resolved feed audio).
  const playMention = (row: EntityCitationRow) => {
    if (!row.audio_url || !row.podcast_slug || !row.episode_slug) return
    const startAt = row.start_ms / 1000
    if (player.isCurrent(row.episode_id)) {
      player.seek(startAt)
      if (!player.isPlaying) player.resume()
      return
    }
    player.play(
      {
        episodeId: row.episode_id,
        podcastSlug: row.podcast_slug,
        episodeSlug: row.episode_slug,
        title: row.episode_title,
        podcastTitle: row.podcast_title,
        audioUrl: row.audio_url,
        artworkUrl: row.image_url ?? null,
        durationHint: row.duration ?? null,
      },
      { startAt },
    )
  }

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
  const mostDiscussed = data.most_discussed_on ?? []

  // Spec #45 — enrichment is optional; treat absent as "base layout".
  const enrichment = data.enrichment ?? null
  const facts = enrichment?.facts ?? []
  const affiliations = enrichment?.affiliations ?? []
  const isCompany = data.entity.type === 'company'
  // Headline (Wikidata) is the lead; show the ReFinED description below
  // only when it adds something different.
  const headline = enrichment?.headline ?? null
  const description = data.description && data.description !== headline ? data.description : null
  // Two-column (main + vital-stats sidebar) only when there are facts to show.
  const layoutCls = facts.length > 0
    ? 'lg:grid lg:grid-cols-[1fr_300px] lg:gap-6 space-y-6 lg:space-y-0'
    : 'space-y-6'

  return (
    <div className="space-y-6">
      <nav className="text-sm flex items-center gap-1">
        <Link to="/" className="text-gray-500 hover:text-gray-700">Home</Link>
        <span className="text-gray-400">/</span>
        <span className="text-gray-700">Entities</span>
        <span className="text-gray-400">/</span>
        <span className="text-gray-700">{style.label}s</span>
      </nav>

      {/* Hero */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex flex-col sm:flex-row gap-4 sm:gap-6">
          {enrichment?.image_url && (
            <div className="shrink-0">
              <img
                src={enrichment.image_url}
                alt={data.entity.canonical_name}
                loading="eager"
                className={`w-24 h-24 sm:w-28 sm:h-28 rounded-lg border border-gray-200 ${
                  isCompany ? 'object-contain bg-gray-50 p-2' : 'object-cover'
                }`}
              />
              {enrichment.image_attribution && (
                <p className="mt-1 text-[10px] text-gray-400 text-center">via {enrichment.image_attribution}</p>
              )}
            </div>
          )}
          <div className="flex-1 min-w-0 space-y-2">
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
            {headline && <p className="text-base text-gray-700">{headline}</p>}
            {data.aliases.length > 0 && (
              <div className="text-sm text-gray-500">
                <span className="font-medium text-gray-600">Also known as:</span>{' '}
                {data.aliases.join(', ')}
              </div>
            )}
            {description && <p className="text-sm text-gray-600">{description}</p>}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1">
              {enrichment?.wikipedia_url && (
                <a
                  href={enrichment.wikipedia_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-500 hover:text-gray-700 hover:underline"
                >
                  Wikipedia ↗
                </a>
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
          </div>
        </div>
      </div>

      {/* Main column + optional vital-stats sidebar */}
      <div className={layoutCls}>
        <div className="space-y-6 min-w-0">
          {/* About (Wikipedia lead) */}
          {enrichment?.wikipedia_extract && (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">About</h2>
              <p className="mt-3 text-sm leading-relaxed text-gray-700">{enrichment.wikipedia_extract}</p>
              {enrichment.wikipedia_url && (
                <a
                  href={enrichment.wikipedia_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-block text-xs text-primary-700 hover:underline"
                >
                  Read more on Wikipedia ↗
                </a>
              )}
            </section>
          )}

          {/* Affiliations (founder / CEO / employer cross-links) */}
          {affiliations.length > 0 && (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                {isCompany ? 'People' : 'Affiliations'}
              </h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {affiliations.map((aff, idx) => {
                  const affStyle = aff.entity_type ? entityStyle(aff.entity_type) : null
                  const inner = (
                    <>
                      <span className="opacity-70">{aff.relation}</span>
                      <span className="font-medium">{aff.label}</span>
                    </>
                  )
                  const cls = `inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm ${
                    affStyle
                      ? `${affStyle.pillBg} ${affStyle.pillText} ${affStyle.pillBorder} hover:brightness-95`
                      : 'bg-gray-50 text-gray-700 border-gray-200'
                  }`
                  return aff.entity_id && aff.entity_type ? (
                    <Link key={`${aff.relation}-${aff.entity_id}-${idx}`} to={entityHref(aff.entity_type, aff.entity_id)} className={cls}>
                      {inner}
                    </Link>
                  ) : (
                    <span key={`${aff.relation}-${aff.label}-${idx}`} className={cls}>
                      {inner}
                    </span>
                  )
                })}
              </div>
            </section>
          )}

          {/* Anchor roles — host of, recurring on */}
          {(hostsPodcasts.length > 0 || recurringPodcasts.length > 0) && (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                Podcast roles
              </h2>
              {hostsPodcasts.length > 0 && (
                <div className="mt-3">
                  <div className="text-xs uppercase tracking-wide text-gray-400">Hosts</div>
                  <PodcastRoleList podcasts={hostsPodcasts} keyPrefix="host" />
                </div>
              )}
              {recurringPodcasts.length > 0 && (
                <div className="mt-4">
                  <div className="text-xs uppercase tracking-wide text-gray-400">Recurring on</div>
                  <PodcastRoleList podcasts={recurringPodcasts} keyPrefix="recurring" />
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

          {/* Most discussed on */}
          {mostDiscussed.length > 0 && (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                Most discussed on
              </h2>
              <ul className="mt-3 divide-y divide-gray-100">
                {mostDiscussed.map((p) => (
                  <li key={`discussed-${p.podcast_id}`} className="py-2 flex items-baseline gap-2">
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
                      {p.mention_count} mention{p.mention_count === 1 ? '' : 's'}
                    </span>
                  </li>
                ))}
              </ul>
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
                  const canPlayInline =
                    Boolean(row.audio_url) && Boolean(row.podcast_slug) && Boolean(row.episode_slug)
                  const timestamp = formatTimestamp(row.start_ms)
                  return (
                    <li key={`${row.episode_id}-${row.start_ms}-${idx}`} className="py-3">
                      <div className="flex items-baseline gap-2 text-xs text-gray-500">
                        {canPlayInline ? (
                          <button
                            type="button"
                            onClick={() => playMention(row)}
                            aria-label={`Play "${row.quote}" at ${timestamp}`}
                            className="font-mono tabular-nums text-primary-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 rounded-sm"
                            data-testid="entity-mention-play"
                          >
                            {timestamp}
                          </button>
                        ) : seekHref ? (
                          <Link to={seekHref} className="font-mono tabular-nums text-primary-700 hover:underline">
                            {timestamp}
                          </Link>
                        ) : (
                          <span className="font-mono tabular-nums">{timestamp}</span>
                        )}
                        <span>·</span>
                        {row.speaker && <span>{row.speaker}</span>}
                        <span className="ml-auto">{formatDate(row.published_at)}</span>
                      </div>
                      <p className="mt-1 text-sm text-gray-800">
                        "{boldSurfaceForm(row.quote, row.surface_form)}"
                      </p>
                      <div className="mt-1 text-xs text-gray-500">
                        {seekHref ? (
                          <Link to={seekHref} className="font-medium text-gray-700 hover:underline">
                            {row.episode_title}
                          </Link>
                        ) : (
                          <span className="font-medium text-gray-700">{row.episode_title}</span>
                        )}
                        {' · '}{row.podcast_title}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        </div>

        {/* Vital stats sidebar */}
        {facts.length > 0 && (
          <aside className="space-y-6">
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">Vital stats</h2>
              <dl className="mt-3 space-y-3">
                {facts.map((fact, idx) => (
                  <div key={`${fact.label}-${idx}`}>
                    <dt className="text-xs uppercase tracking-wide text-gray-400">{fact.label}</dt>
                    <dd className="text-sm text-gray-800">
                      {fact.url ? (
                        <a
                          href={fact.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary-700 hover:underline break-words"
                        >
                          {fact.value} ↗
                        </a>
                      ) : (
                        fact.value
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          </aside>
        )}
      </div>
    </div>
  )
}
