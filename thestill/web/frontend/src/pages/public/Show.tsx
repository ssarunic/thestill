// Spec #94 — a public show: who hosts it, where to listen, and its
// summarised episodes. Following asks for sign-in and returns here.
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { PublicNotFound, entityPath, episodePath, formatDate } from '../../api/public'
import {
  Avatar,
  Button,
  GuestLine,
  ListenOn,
  SignInCard,
  Skeleton,
} from '../../components/public/ui'
import { useSignIn } from '../../components/public/helpers'
import { usePublicPodcast, usePublicPodcastEpisodes } from '../../hooks/usePublicApi'
import NotFound, { ModuleError } from './NotFound'

export default function Show() {
  const { podcastSlug = '' } = useParams()
  const podcast = usePublicPodcast(podcastSlug)
  const episodes = usePublicPodcastEpisodes(podcastSlug)
  const signIn = useSignIn()
  const [expanded, setExpanded] = useState(false)

  if (podcast.error instanceof PublicNotFound) return <NotFound />
  const show = podcast.data?.podcast
  const items = episodes.data?.pages.flatMap((page) => page.episodes) ?? []
  const people = [...(show?.hosts ?? []), ...(show?.recurring ?? [])]

  return (
    <div className="ts-container">
      <section className="ts-hero">
        {show ? (
          <Avatar src={show.image_url} name={show.title} size={128} shape="square" eager />
        ) : (
          <Skeleton width={128} height={128} />
        )}
        <div className="ts-hero-body">
          {show ? (
            <>
              <span className="ts-eyebrow">
                Podcast{show.author ? ` · ${show.author}` : ''}
                {show.explicit ? ' · Explicit' : ''}
              </span>
              <h1 className="ts-page-title">{show.title}</h1>
              {show.description && (
                <p className={`ts-standfirst ts-description ${expanded ? '' : 'ts-clamp-5'}`} style={{ fontFamily: 'var(--font-sans)', fontSize: 15, lineHeight: '23px' }}>
                  {show.description}
                </p>
              )}
              {show.description && show.description.length > 280 && (
                <div>
                  <button type="button" className="ts-btn ts-btn-text" onClick={() => setExpanded((v) => !v)}>
                    {expanded ? 'Less' : 'More'}
                  </button>
                </div>
              )}
              <div className="ts-meta">
                <span>{show.summarised_count} summarised episodes</span>
                {show.latest_pub_date && (
                  <>
                    <span className="ts-sep" aria-hidden="true">
                      ·
                    </span>
                    <span>latest {formatDate(show.latest_pub_date)}</span>
                  </>
                )}
              </div>
              <div style={{ marginTop: 8 }}>
                <Button onClick={signIn}>Follow</Button>
              </div>
              {show.copyright && <span className="ts-meta" style={{ fontSize: 11 }}>{show.copyright}</span>}
            </>
          ) : (
            <>
              <Skeleton width={120} height={14} />
              <Skeleton width="70%" height={36} />
              <Skeleton width="90%" height={18} />
            </>
          )}
        </div>
      </section>

      <div className="ts-columns" style={{ paddingTop: 32 }}>
        <div className="ts-stack">
          {show && (
            <ListenOn
              links={[
                { platform: 'apple', url: show.apple_url },
                { platform: 'spotify', url: show.spotify_url },
                { platform: 'youtube', url: show.youtube_url },
              ]}
            />
          )}
          {people.length > 0 && (
            <section className="ts-section">
              <span className="ts-rail-label">Hosted by</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24 }}>
                {people.slice(0, 4).map((person) => (
                  <Link key={person.slug} to={entityPath(person)} className="ts-ep-head">
                    <Avatar name={person.name} size={48} />
                    <span className="ts-personrow-name">{person.name}</span>
                  </Link>
                ))}
              </div>
            </section>
          )}
          <section className="ts-section" aria-labelledby="episodes">
            <div className="ts-section-head">
              <h2 className="ts-h2" id="episodes">
                Latest episodes
              </h2>
            </div>
            {episodes.isError && <ModuleError onRetry={() => episodes.refetch()} />}
            {episodes.isLoading &&
              Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="ts-eprow" aria-hidden="true">
                  <Skeleton width={140} height={12} />
                  <Skeleton width="80%" height={20} />
                  <Skeleton width="95%" height={14} />
                </div>
              ))}
            {!episodes.isLoading && items.length === 0 && !episodes.isError && (
              <p className="ts-muted">Summaries for this show are on their way.</p>
            )}
            <div>
              {items.map((episode) => (
                <article key={episode.slug} className="ts-eprow">
                  <div className="ts-meta">
                    <span>{formatDate(episode.pub_date)}</span>
                    {episode.duration_formatted && (
                      <>
                        <span className="ts-sep" aria-hidden="true">
                          ·
                        </span>
                        <span>{episode.duration_formatted}</span>
                      </>
                    )}
                  </div>
                  <Link to={episodePath(podcastSlug, episode.slug)} className="ts-eprow-title">
                    {episode.title}
                  </Link>
                  {episode.summary_preview && <p className="ts-eprow-desc">{episode.summary_preview}</p>}
                  <GuestLine guests={episode.guests} />
                </article>
              ))}
            </div>
            {episodes.hasNextPage && (
              <div>
                <Button variant="text" onClick={() => episodes.fetchNextPage()}>
                  {episodes.isFetchingNextPage ? 'Loading…' : 'Show more episodes'}
                </Button>
              </div>
            )}
          </section>
          {show && (
            <SignInCard title={`Get every new episode of ${show.title} summarised in your inbox.`} buttonLabel="Follow with Google">
              Follow the show and each new episode&apos;s summary arrives in your thestill inbox.
            </SignInCard>
          )}
        </div>
        <aside className="ts-rail" aria-label="About this show">
          {show?.website_url && (
            <section className="ts-rail-module">
              <h3 className="ts-rail-label">Website</h3>
              <a href={show.website_url} className="ts-link" target="_blank" rel="noopener noreferrer" style={{ overflowWrap: 'anywhere' }}>
                {show.website_url.replace(/^https?:\/\//, '').replace(/\/$/, '')}
              </a>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
