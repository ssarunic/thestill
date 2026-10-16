// Spec #94 — person and entity pages: "who said what across shows".
// People: what they said (filterable by topic, the filter lives in the URL
// as ?about=), where they're heard, what others say about them. Things:
// who's talking about it and the latest quotes naming it.
import { Fragment, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { PublicNotFound, entityPath, formatDate, type PublicEntityType, type PublicQuote } from '../../api/public'
import {
  ActivitySparkline,
  Avatar,
  Button,
  EntityDot,
  EpisodeGroupHead,
  FactList,
  FilterChip,
  QuoteCard,
  QuoteSkeleton,
  SignInCard,
  Skeleton,
} from '../../components/public/ui'
import { groupByEpisode, typeLabel } from '../../components/public/helpers'
import { usePublicEntity, usePublicEntityQuotes } from '../../hooks/usePublicApi'
import NotFound, { ModuleError } from './NotFound'

const ENTITY_TYPES: PublicEntityType[] = ['person', 'company', 'product', 'topic']

function QuoteStream({
  quotes,
  showSpeaker,
  hasMore,
  loading,
  onMore,
}: {
  quotes: PublicQuote[]
  showSpeaker: boolean
  hasMore: boolean
  loading: boolean
  onMore: () => void
}) {
  const groups = groupByEpisode(quotes)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  return (
    <div className="ts-quote-list">
      {groups.map((group) => (
        <div key={group.key} className="ts-ep-group">
          <EpisodeGroupHead quote={group.quotes[0]} />
          {(expanded.has(group.key) ? group.quotes : group.quotes.slice(0, 2)).map((quote) => (
            <QuoteCard
              key={`${quote.episode.slug}-${quote.start_seconds}-${quote.about.id}`}
              quote={quote}
              showSpeaker={showSpeaker}
              showShow={false}
              showDate={false}
            />
          ))}
          {group.quotes.length > 2 && !expanded.has(group.key) && (
            <button
              type="button"
              className="ts-link ts-link-button"
              style={{ fontSize: 13 }}
              onClick={() => setExpanded((prev) => new Set(prev).add(group.key))}
            >
              +{group.quotes.length - 2} more from this episode
            </button>
          )}
        </div>
      ))}
      {loading && <QuoteSkeleton />}
      {hasMore && !loading && (
        <div>
          <Button variant="text" onClick={onMore}>
            Show more quotes
          </Button>
        </div>
      )}
    </div>
  )
}

export default function EntityPage() {
  const { entityType = '', slug = '' } = useParams()
  const [params] = useSearchParams()
  const validType = ENTITY_TYPES.includes(entityType as PublicEntityType)
  const entity = usePublicEntity(entityType, slug)
  const data = entity.data
  const isPerson = entityType === 'person'

  const aboutSlug = params.get('about')
  const aboutTopic = useMemo(
    () => (aboutSlug && data ? data.topics.find((t) => t.slug === aboutSlug) ?? null : null),
    [aboutSlug, data],
  )
  const said = usePublicEntityQuotes(entityType, slug, 'said', aboutTopic?.id ?? null, isPerson && !!data && data.quote_counts.said > 0)
  const about = usePublicEntityQuotes(entityType, slug, 'about', null, !!data && data.quote_counts.about > 0)

  if (!validType || entity.error instanceof PublicNotFound) return <NotFound />
  if (entity.isError) {
    return (
      <div className="ts-container" style={{ paddingTop: 40 }}>
        <ModuleError onRetry={() => entity.refetch()} />
      </div>
    )
  }

  const name = data?.entity.name ?? ''
  const enrichment = data?.enrichment ?? null
  const headline = enrichment?.headline || data?.entity.description || null
  const hostOf = data?.roles.hosts[0]
  const saidQuotes = said.data?.pages.flatMap((p) => p.quotes) ?? []
  const aboutQuotes = about.data?.pages.flatMap((p) => p.quotes) ?? []
  const stats = data?.stats
  const featured = aboutTopic ? saidQuotes[0] ?? null : data?.featured_quote ?? null
  // Wikidata lists one affiliation per relation (founder, CEO, …); show
  // each person once with their relations joined.
  const people: { slug: string; label: string; relations: string[] }[] = []
  if (!isPerson) {
    for (const aff of enrichment?.affiliations ?? []) {
      if (aff.ref?.type !== 'person') continue
      const existing = people.find((p) => p.slug === aff.ref!.slug)
      if (!existing) people.push({ slug: aff.ref.slug, label: aff.label, relations: [aff.relation] })
      else if (!existing.relations.includes(aff.relation)) existing.relations.push(aff.relation)
    }
  }

  return (
    <div className="ts-container">
      <section className="ts-hero">
        {data ? (
          <div style={{ flex: 'none', textAlign: 'center' }}>
            {isPerson || enrichment?.image_url ? (
              <Avatar src={enrichment?.image_url} name={name} size={112} shape={isPerson ? 'round' : 'square'} logo={!isPerson} eager />
            ) : null}
            {enrichment?.image_url && enrichment.image_attribution && <div className="ts-photo-credit">{isPerson ? 'Photo' : 'Image'}: Wikimedia Commons</div>}
          </div>
        ) : (
          <Skeleton width={112} height={112} round={isPerson} />
        )}
        <div className="ts-hero-body">
          {data ? (
            <>
              <span className="ts-eyebrow">
                <EntityDot type={data.entity.type} />
                <span>{typeLabel(data.entity.type)}</span>
                {hostOf && (
                  <>
                    <span className="ts-sep" aria-hidden="true">
                      ·
                    </span>
                    <Link to={`/podcasts/${hostOf.slug}`}>Host of {hostOf.title}</Link>
                  </>
                )}
              </span>
              <h1 className="ts-page-title">{name}</h1>
              {headline && <p className="ts-standfirst">{headline}</p>}
              {aboutTopic && (
                <p className="ts-meta" style={{ fontSize: 15 }}>
                  On {aboutTopic.name} · {aboutTopic.quotes} quote{aboutTopic.quotes === 1 ? '' : 's'}
                </p>
              )}
              {stats && stats.episodes > 0 && (
                <p className="ts-meta" style={{ margin: 0 }}>
                  {isPerson ? 'Heard on' : 'Discussed on'} {stats.shows} show{stats.shows === 1 ? '' : 's'}
                  <span className="ts-sep" aria-hidden="true">
                    ·
                  </span>
                  {stats.episodes} episode{stats.episodes === 1 ? '' : 's'}
                  {!isPerson && stats.last_30_days > 0 && (
                    <>
                      <span className="ts-sep" aria-hidden="true">
                        ·
                      </span>
                      {stats.last_30_days} in the last 30 days
                    </>
                  )}
                  {isPerson && stats.since && (
                    <>
                      <span className="ts-sep" aria-hidden="true">
                        ·
                      </span>
                      since {new Date(stats.since).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })}
                    </>
                  )}
                </p>
              )}
            </>
          ) : (
            <>
              <Skeleton width={140} height={14} />
              <Skeleton width="60%" height={40} />
              <Skeleton width="80%" height={18} />
            </>
          )}
        </div>
      </section>

      {featured && (
        <div style={{ padding: '8px 0 8px', borderBottom: '1px solid var(--divider)' }}>
          <QuoteCard quote={featured} variant="featured" showSpeaker={!isPerson} />
        </div>
      )}

      {isPerson && data && data.topics.length >= 2 && (
        <div className="ts-sticky-filter">
          <div className="ts-chips" role="group" aria-label="Filter quotes by topic">
            <FilterChip selected={!aboutTopic} to="?">
              All
            </FilterChip>
            {data.topics.map((topic) => (
              <FilterChip key={topic.id} type={topic.type} selected={aboutTopic?.id === topic.id} to={`?about=${topic.slug}`}>
                {topic.name}
              </FilterChip>
            ))}
          </div>
        </div>
      )}

      <div className="ts-columns" style={{ paddingTop: 32 }}>
        <div className="ts-stack">
          {!isPerson && data && data.speakers.length > 0 && (
            <section className="ts-section" aria-labelledby="whos-talking">
              <div className="ts-section-head">
                <h2 className="ts-h2" id="whos-talking">
                  Who&apos;s talking
                </h2>
              </div>
              <div>
                {data.speakers.map((person) => (
                  <Link key={person.slug} to={`/entities/person/${person.slug}?about=${data.entity.slug}`} className="ts-personrow">
                    <Avatar src={person.image_url} name={person.name} size={40} />
                    <div className="ts-personrow-main">
                      <div className="ts-personrow-head">
                        <span className="ts-personrow-name">{person.name}</span>
                      </div>
                      {person.quote && <p className="ts-personrow-quote">{`“${person.quote}”`}</p>}
                    </div>
                    <div className="ts-personrow-counts">
                      <span className="ts-personrow-count">
                        {person.quotes} quote{person.quotes === 1 ? '' : 's'}
                      </span>
                      <span>
                        {person.shows} show{person.shows === 1 ? '' : 's'}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {isPerson && data && data.quote_counts.said > 0 && (
            <section className="ts-section" aria-labelledby="what-they-said">
              <div className="ts-section-head">
                <h2 className="ts-h2" id="what-they-said">
                  {aboutTopic ? `What ${name} said about ${aboutTopic.name}` : 'What they said'}
                </h2>
              </div>
              {said.isError && <ModuleError onRetry={() => said.refetch()} />}
              <QuoteStream
                quotes={saidQuotes}
                showSpeaker={false}
                hasMore={!!said.hasNextPage}
                loading={said.isLoading || said.isFetchingNextPage}
                onMore={() => said.fetchNextPage()}
              />
            </section>
          )}

          {isPerson && data && (data.roles.hosts.length + data.roles.recurring.length > 0 || data.roles.guest_episodes.length > 0) && (
            <section className="ts-section" aria-labelledby="appears-on">
              <div className="ts-section-head">
                <h2 className="ts-h2" id="appears-on">
                  Appears on
                </h2>
              </div>
              <div className="ts-tiles">
                {[
                  ...data.roles.hosts.map((r) => ({ ...r, role: 'Host' })),
                  ...data.roles.recurring.map((r) => ({ ...r, role: 'Recurring' })),
                ].map((r) => (
                  <Link key={`${r.role}-${r.slug}`} to={`/podcasts/${r.slug}`} className="ts-showtile">
                    <span className="ts-showtile-title">{r.title}</span>
                    <span className="ts-showtile-meta">
                      {r.role}
                      {r.count != null ? ` · ${r.count} episodes` : ''}
                    </span>
                  </Link>
                ))}
              </div>
              {data.roles.guest_episodes.length > 0 && (
                <div>
                  <span className="ts-rail-label">Guest on</span>
                  <ul className="ts-related">
                    {data.roles.guest_episodes.map((g) => (
                      <li key={`${g.podcast_slug}/${g.episode_slug}`} style={{ display: 'block' }}>
                        <Link to={`/podcasts/${g.podcast_slug}/episodes/${g.episode_slug}`} style={{ display: 'block' }}>
                          <span className="ts-meta" style={{ display: 'block' }}>
                            {g.podcast_title}
                            {g.published_at ? ` · ${formatDate(g.published_at)}` : ''}
                          </span>
                          <span style={{ fontWeight: 600 }}>{g.episode_title}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}

          {data && data.quote_counts.about > 0 && !aboutTopic && (
            <section className="ts-section" aria-labelledby="others-say">
              <div className="ts-section-head">
                <h2 className="ts-h2" id="others-say">
                  {isPerson ? `What others say about ${name}` : `Latest on ${name}`}
                </h2>
              </div>
              {about.isError && <ModuleError onRetry={() => about.refetch()} />}
              <QuoteStream
                quotes={aboutQuotes}
                showSpeaker
                hasMore={!!about.hasNextPage}
                loading={about.isLoading || about.isFetchingNextPage}
                onMore={() => about.fetchNextPage()}
              />
            </section>
          )}

          {data && data.quote_counts.said + data.quote_counts.about === 0 && (
            <p className="ts-muted">
              No quotes yet.{data.roles.hosts.length > 0 ? ' Hosts often go unnamed in their own show.' : ''}
            </p>
          )}

          {data && (
            <SignInCard
              title={isPerson ? `Everything ${name} said, word for word.` : `Search every mention of ${name} across every show you follow.`}
              buttonLabel="See every mention"
            >
              Sign in to read the full transcripts, search every show, and get a summary of each new episode in your inbox.
            </SignInCard>
          )}
        </div>

        <aside className="ts-rail" aria-label={`About ${name}`}>
          {data && <ActivitySparkline weeks={data.activity.weeks} start={data.activity.start} />}
          {enrichment?.wikipedia_extract && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">
                About <span className="ts-rail-source">(from Wikipedia)</span>
              </h3>
              <p className="ts-about ts-clamp-5">{enrichment.wikipedia_extract}</p>
              {enrichment.wikipedia_url && (
                <a href={enrichment.wikipedia_url} className="ts-link" target="_blank" rel="noopener noreferrer" style={{ fontSize: 13 }}>
                  Read on Wikipedia ↗
                </a>
              )}
            </section>
          )}
          {enrichment && enrichment.facts.length > 0 && (
            <FactList title="Facts" source="Wikidata" items={enrichment.facts.map((f) => ({ label: f.label, value: f.value, href: f.url }))} />
          )}
          {people.length > 0 && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">People</h3>
              <ul className="ts-related">
                {people.map((p) => (
                  <li key={p.slug}>
                    <Link to={entityPath({ type: 'person', slug: p.slug })}>
                      <EntityDot type="person" />
                      <span>{p.label}</span>
                    </Link>
                    <span className="ts-related-count">{p.relations.join(' · ')}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {data && data.most_discussed_on.length > 0 && !isPerson && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">Most discussed on</h3>
              <ul className="ts-related">
                {data.most_discussed_on.map((show) => (
                  <li key={show.slug}>
                    <Link to={`/podcasts/${show.slug}`}>{show.title}</Link>
                    {show.count != null && <span className="ts-related-count">{show.count}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {data && data.related.length >= 3 && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">Often discussed with</h3>
              <ul className="ts-related">
                {data.related.map((other) => (
                  <Fragment key={`${other.type}-${other.slug}`}>
                    <li>
                      <Link to={entityPath(other)}>
                        <EntityDot type={other.type} />
                        <span>{other.name}</span>
                      </Link>
                      <span className="ts-related-count">{other.episode_count} ep</span>
                    </li>
                  </Fragment>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
