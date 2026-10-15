// Spec #94 — a public episode: the summary, who spoke and their best
// lines. The transcript exists behind sign-in; the tab says so.
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { PublicNotFound, entityPath, episodePath, formatDate } from '../../api/public'
import {
  Avatar,
  EntityDot,
  FilterChip,
  ListenOn,
  QuoteCard,
  QuoteSkeleton,
  SignInCard,
  Skeleton,
} from '../../components/public/ui'
import { typeLabel } from '../../components/public/helpers'
import {
  usePublicEpisode,
  usePublicEpisodeQuotes,
  usePublicPodcastEpisodes,
  usePublicSummary,
} from '../../hooks/usePublicApi'
import NotFound, { ModuleError } from './NotFound'

const SummaryViewer = lazy(() => import('../../components/SummaryViewer'))

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width={13} height={13} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
      <rect x={5} y={11} width={14} height={10} rx={2} />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  )
}

export default function Episode() {
  const { podcastSlug = '', episodeSlug = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const q = params.get('q')
  const linkedSeconds = q && /^\d{1,6}$/.test(q) ? Number(q) : null
  const tab = params.get('view') === 'transcript' ? 'transcript' : 'summary'
  const [speaker, setSpeaker] = useState<string | null>(null)

  const episodeQuery = usePublicEpisode(podcastSlug, episodeSlug)
  const summary = usePublicSummary(podcastSlug, episodeSlug)
  const quotes = usePublicEpisodeQuotes(podcastSlug, episodeSlug, linkedSeconds)
  const more = usePublicPodcastEpisodes(podcastSlug)

  const linked = quotes.data?.linked ?? null
  const linkedId = linked ? `quote-${linked.start_seconds}` : null
  useEffect(() => {
    if (!linkedId) return
    const el = document.getElementById(linkedId)
    if (!el) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  }, [linkedId])

  const speakers = useMemo(() => {
    const names = new Map<string, string>()
    for (const quote of quotes.data?.quotes ?? []) names.set(quote.speaker.slug, quote.speaker.name)
    return [...names.entries()]
  }, [quotes.data])

  if (episodeQuery.error instanceof PublicNotFound) {
    return <NotFound title="This episode isn't summarised yet" message="Its show may have newer episodes with summaries." />
  }
  const episode = episodeQuery.data?.episode
  const setTab = (next: 'summary' | 'transcript') => {
    const nextParams = new URLSearchParams(params)
    if (next === 'transcript') nextParams.set('view', 'transcript')
    else nextParams.delete('view')
    setParams(nextParams, { replace: true })
  }
  const people = episode ? [...episode.hosts, ...episode.guests] : []
  const youtube = episode?.platform_links.find((l) => l.platform === 'youtube')?.url ?? null
  const shown = (quotes.data?.quotes ?? []).filter((quote) => !speaker || quote.speaker.slug === speaker)
  const linkedInList = linked && shown.some((quote) => quote.start_seconds === linked.start_seconds)
  const moreEpisodes = (more.data?.pages[0]?.episodes ?? []).filter((e) => e.slug !== episodeSlug).slice(0, 3)

  return (
    <div className="ts-container">
      <section className="ts-hero">
        {episode ? <Avatar src={episode.image_url} name={episode.title} size={112} shape="square" eager /> : <Skeleton width={112} height={112} />}
        <div className="ts-hero-body">
          {episode ? (
            <>
              <span className="ts-eyebrow">
                <Link to={`/podcasts/${episode.podcast.slug}`}>{episode.podcast.title}</Link>
                {episode.pub_date && <span>· {formatDate(episode.pub_date)}</span>}
                {episode.duration_formatted && <span>· {episode.duration_formatted}</span>}
              </span>
              <h1 className="ts-episode-title">{episode.title}</h1>
              {people.length > 0 && (
                <div className="ts-eprow-guests" style={{ fontSize: 15 }}>
                  <span>With </span>
                  {people.slice(0, 4).map((p, i) => (
                    <span key={p.slug}>
                      {i > 0 && (i === Math.min(people.length, 4) - 1 ? ' and ' : ', ')}
                      <Link to={entityPath(p)}>{p.name}</Link>
                    </span>
                  ))}
                  {people.length > 4 && <span> and {people.length - 4} others</span>}
                </div>
              )}
              <ListenOn
                label={false}
                links={[
                  { platform: 'apple', url: episode.platform_links.find((l) => l.platform === 'apple')?.url ?? episode.podcast.apple_url },
                  { platform: 'spotify', url: episode.platform_links.find((l) => l.platform === 'spotify')?.url ?? episode.podcast.spotify_url },
                  { platform: 'youtube', url: youtube ?? episode.podcast.youtube_url },
                ]}
              />
            </>
          ) : (
            <>
              <Skeleton width={180} height={14} />
              <Skeleton width="80%" height={30} />
            </>
          )}
        </div>
      </section>

      <div className="ts-columns" style={{ paddingTop: 32 }}>
        <div className="ts-stack">
          <div className="ts-tabs">
            <div role="tablist" className="ts-tabs-list">
              <button type="button" role="tab" aria-selected={tab === 'summary'} className={`ts-tab ${tab === 'summary' ? 'ts-tab-active' : ''}`} onClick={() => setTab('summary')}>
                Summary
              </button>
              <button type="button" role="tab" aria-selected={tab === 'transcript'} className={`ts-tab ${tab === 'transcript' ? 'ts-tab-active' : ''}`} onClick={() => setTab('transcript')}>
                <span>Transcript</span>
                <span className="ts-tab-lock">
                  <LockIcon />
                  <span className="ts-visually-hidden"> (sign in to read)</span>
                </span>
              </button>
            </div>
          </div>

          {tab === 'transcript' ? (
            <SignInCard title="The full transcript, with speaker names and timestamps, is one sign-in away.">
              Sign in to read every word, search across every show, and get each new episode summarised in your inbox.
            </SignInCard>
          ) : (
            <article className="ts-summary ts-fade-in">
              {summary.isLoading && (
                <>
                  <Skeleton width="100%" height={16} />
                  <div style={{ height: 10 }} />
                  <Skeleton width="92%" height={16} />
                  <div style={{ height: 10 }} />
                  <Skeleton width="85%" height={16} />
                </>
              )}
              {summary.isError && <ModuleError onRetry={() => summary.refetch()} />}
              {summary.data && (
                <Suspense fallback={<Skeleton width="100%" height={200} />}>
                  <SummaryViewer content={summary.data.content} available />
                </Suspense>
              )}
            </article>
          )}

          {(quotes.isLoading || (quotes.data && (quotes.data.quotes.length >= 2 || linked))) && (
            <section className="ts-section" aria-labelledby="episode-quotes">
              <div className="ts-section-head">
                <h2 className="ts-h2" id="episode-quotes">
                  Quotes from this episode
                </h2>
              </div>
              {speakers.length > 1 && (
                <div className="ts-chips" role="group" aria-label="Filter by speaker">
                  <FilterChip selected={!speaker} onClick={() => setSpeaker(null)}>
                    All
                  </FilterChip>
                  {speakers.map(([slug, name]) => (
                    <FilterChip key={slug} selected={speaker === slug} onClick={() => setSpeaker(slug)}>
                      {name}
                    </FilterChip>
                  ))}
                </div>
              )}
              <div className="ts-quote-list">
                {quotes.isLoading && (
                  <>
                    <QuoteSkeleton />
                    <QuoteSkeleton />
                  </>
                )}
                {linked && !linkedInList && !speaker && (
                  <QuoteCard quote={linked} variant="linked" label="Linked quote" showShow={false} showDate={false} youtubeUrl={youtube} id={linkedId ?? undefined} className="ts-highlight" />
                )}
                {shown.map((quote) => {
                  const isLinked = linked && quote.start_seconds === linked.start_seconds
                  return (
                    <QuoteCard
                      key={`${quote.start_seconds}-${quote.about.id}`}
                      quote={quote}
                      variant={isLinked ? 'linked' : 'stream'}
                      label={isLinked ? 'Linked quote' : undefined}
                      showShow={false}
                      showDate={false}
                      youtubeUrl={youtube}
                      id={isLinked ? linkedId ?? undefined : undefined}
                      className={isLinked ? 'ts-highlight' : undefined}
                    />
                  )
                })}
              </div>
            </section>
          )}

          {tab === 'summary' && (
            <SignInCard title="The full transcript, with speaker names and timestamps, is one sign-in away.">
              Sign in to read every word, with speaker names and timestamps.
            </SignInCard>
          )}
        </div>

        <aside className="ts-rail" aria-label="About this episode">
          {quotes.data && quotes.data.mentioned.length >= 3 && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">Mentioned in this episode</h3>
              <ul className="ts-related">
                {quotes.data.mentioned.map((entity) => (
                  <li key={`${entity.type}-${entity.slug}`}>
                    <Link to={entityPath(entity)}>
                      <EntityDot type={entity.type} />
                      <span>{entity.name}</span>
                      <span className="ts-visually-hidden"> ({typeLabel(entity.type)})</span>
                    </Link>
                    <span className="ts-related-count">{entity.count}×</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {episode?.description && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">From the publisher</h3>
              <p className="ts-about ts-description ts-clamp-5 ts-muted" style={{ fontSize: 14, lineHeight: '21px' }}>
                {episode.description}
              </p>
            </section>
          )}
          {moreEpisodes.length > 0 && episode && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">More from {episode.podcast.title}</h3>
              <ul className="ts-related">
                {moreEpisodes.map((e) => (
                  <li key={e.slug} style={{ display: 'block' }}>
                    <Link to={episodePath(podcastSlug, e.slug)} style={{ display: 'block' }}>
                      <span className="ts-meta" style={{ display: 'block' }}>
                        {formatDate(e.pub_date)}
                      </span>
                      <span style={{ fontWeight: 600 }}>{e.title}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
