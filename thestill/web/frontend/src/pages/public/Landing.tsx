// Spec #94 — the landing page: what thestill is in one screen, one real
// summary, one subject heard across several shows, and what signing in adds.
import { Suspense } from 'react'
import { Link } from 'react-router-dom'
import { entityPath, episodePath, formatDate } from '../../api/public'
import {
  Avatar,
  EntityDot,
  EpisodeRow,
  QuoteCard,
  QuoteSkeleton,
  Skeleton,
} from '../../components/public/ui'
import { PublicSummaryViewer as SummaryViewer } from '../../components/public/summaryViewer'
import { usePublicRecentSummaries, usePublicSummary, usePublicWhoSaidWhat } from '../../hooks/usePublicApi'


function LatestSummary() {
  const recent = usePublicRecentSummaries(1)
  const episode = recent.data?.episodes[0]
  const summary = usePublicSummary(episode?.podcast?.slug ?? '', episode?.slug ?? '')
  if (recent.isLoading) {
    return <Skeleton width="100%" height={220} />
  }
  if (!episode || !episode.podcast) return null
  const href = episodePath(episode.podcast.slug, episode.slug)
  return (
    <section className="ts-section" aria-labelledby="latest-summary">
      <div className="ts-section-head">
        <h2 className="ts-h2" id="latest-summary">
          Latest summary
        </h2>
        <span className="ts-meta">{formatDate(episode.pub_date)}</span>
      </div>
      <div className="ts-eprow-show">
        <Avatar src={episode.image_url} name={episode.podcast.title} size={56} shape="square" />
        <div style={{ minWidth: 0 }}>
          <Link to={`/podcasts/${episode.podcast.slug}`} className="ts-link">
            {episode.podcast.title}
          </Link>
          <div>
            <Link to={href} className="ts-eprow-title">
              {episode.title}
            </Link>
          </div>
        </div>
      </div>
      <div className="ts-summary">
        {summary.data ? (
          <Suspense fallback={<Skeleton width="100%" height={160} />}>
            <SummaryViewer content={summary.data.content} available />
          </Suspense>
        ) : (
          <p className="ts-standfirst">{episode.summary_preview}</p>
        )}
      </div>
      <p>
        <Link to={href} className="ts-link">
          Read the whole summary →
        </Link>
      </p>
    </section>
  )
}

function WhoSaidWhat() {
  const { data, isLoading } = usePublicWhoSaidWhat()
  if (isLoading) {
    return (
      <div className="ts-quote-list">
        <QuoteSkeleton />
        <QuoteSkeleton />
      </div>
    )
  }
  if (!data?.entity || !data.quotes.length) return null
  const when = data.days === 7 ? 'this week' : 'this month'
  return (
    <section className="ts-section" aria-labelledby="who-said-what">
      <div className="ts-section-head">
        <h2 className="ts-h2" id="who-said-what">
          Who said what {when}: {data.entity.name}
        </h2>
        <Link to={entityPath(data.entity)} className="ts-link">
          <EntityDot type={data.entity.type} /> See all →
        </Link>
      </div>
      <p className="ts-muted" style={{ margin: 0 }}>
        Discussed by {data.speakers} speakers on {data.shows} show{data.shows === 1 ? '' : 's'}.
      </p>
      <div className="ts-quote-list">
        {data.quotes.map((quote) => (
          <QuoteCard key={`${quote.episode.slug}-${quote.start_seconds}`} quote={quote} />
        ))}
      </div>
    </section>
  )
}

function FreshThisWeek() {
  const { data, isLoading } = usePublicRecentSummaries(6)
  if (isLoading) return <Skeleton width="100%" height={240} />
  if (!data?.episodes.length) return null
  return (
    <section className="ts-section" aria-labelledby="fresh">
      <div className="ts-section-head">
        <h2 className="ts-h2" id="fresh">
          Recently summarised
        </h2>
      </div>
      <div>
        {data.episodes.map((episode) =>
          episode.podcast ? (
            <EpisodeRow
              key={`${episode.podcast.slug}/${episode.slug}`}
              episode={episode}
              podcastSlug={episode.podcast.slug}
              podcastTitle={episode.podcast.title}
              showPodcast
            />
          ) : null,
        )}
      </div>
    </section>
  )
}

const OUTCOMES = [
  ['Your inbox', 'Every new episode of the shows you follow, summarised the morning it drops.'],
  ['Your briefing', 'One page across all your shows each morning, narrated if you like.'],
  ['Your knowledge base', 'Search every mention of anything across every show you follow, and ask Claude about it.'],
]

export default function Landing() {
  return (
    <div className="ts-container">
      <section className="ts-hero" style={{ flexDirection: 'column', gap: 16, paddingTop: 56 }}>
        <h1 className="ts-page-title" style={{ fontSize: 'clamp(32px, 6vw, 48px)', lineHeight: 1.1 }}>
          Read your podcasts.
        </h1>
        <p className="ts-standfirst">
          thestill turns every episode you follow into a summary you can read in three minutes, with every quote
          attributed and timed, and a morning briefing across all of them.
        </p>
      </section>
      <div className="ts-stack" style={{ maxWidth: 'var(--main-column)', paddingTop: 40 }}>
        <LatestSummary />
        <WhoSaidWhat />
        <section className="ts-section" aria-labelledby="outcomes">
          <div className="ts-section-head">
            <h2 className="ts-h2" id="outcomes">
              What you get when you sign in
            </h2>
          </div>
          <dl style={{ margin: 0, display: 'grid', gap: 16 }}>
            {OUTCOMES.map(([title, body]) => (
              <div key={title}>
                <dt style={{ fontWeight: 600 }}>{title}</dt>
                <dd className="ts-muted" style={{ margin: '4px 0 0' }}>
                  {body}
                </dd>
              </div>
            ))}
          </dl>
        </section>
        <FreshThisWeek />
      </div>
    </div>
  )
}
