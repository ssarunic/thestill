import { Link, useLocation } from 'react-router-dom'
import type { BriefingEpisode, BriefingPodcastGroup } from '../api/types'
import { formatEyebrowDate, formatLength } from '../utils/episodeFormat'
import Artwork from './Artwork'
import { artworkFrameClass } from './artworkRoles'
import ListRow, { ListRowArtwork } from './ListRow'

/** ``58 min`` / ``1 h 10 min`` — whole minutes; seconds are noise on a card. */
function formatCardLength(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null
  return formatLength(Math.max(1, Math.round(seconds / 60)) * 60)
}

interface EpisodeRowProps {
  podcast: BriefingPodcastGroup
  episode: BriefingEpisode
}

/**
 * One episode of the briefing in the inbox's row anatomy: artwork leading
 * at 64 px (taller than the inbox's 48 px because the gist stacks under the
 * title), title, then the gist and a date · length line in the footer.
 */
function EpisodeRow({ podcast, episode }: EpisodeRowProps) {
  // Spec #52 — carrying the page location in navigation state opens the
  // episode in the reader overlay above the still-mounted briefing.
  const location = useLocation()
  const href = `/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`
  const meta = [formatEyebrowDate(episode.pub_date), formatCardLength(episode.duration)].filter(Boolean)

  return (
    <ListRow
      align="start"
      to={href}
      state={{ backgroundLocation: location }}
      // Episode artwork first (matches the reader header), podcast artwork
      // as the fallback when the feed item carries none.
      leading={<ListRowArtwork size={16} sources={[episode.image_url, podcast.image_url]} />}
      title={episode.title}
      titleClassName="font-semibold text-ink group-hover:text-primary-600"
      footer={
        episode.summary_preview || meta.length > 0 ? (
          <div className="mt-1 space-y-1.5">
            {episode.summary_preview && (
              <p className="line-clamp-2 text-sm leading-relaxed text-muted sm:line-clamp-3">
                {episode.summary_preview}
              </p>
            )}
            {meta.length > 0 && <p className="text-xs text-muted">{meta.join(' · ')}</p>}
          </div>
        ) : undefined
      }
    />
  )
}

function PodcastGroup({ podcast }: { podcast: BriefingPodcastGroup }) {
  const headingId = `briefing-podcast-${podcast.id}`
  const count = podcast.episodes.length
  return (
    <section aria-labelledby={headingId}>
      {/* The "publisher" line: small show artwork and name above its episodes. */}
      <header className="flex items-center gap-2.5 border-b border-hairline pb-2">
        <Artwork role="inline" sources={[podcast.image_url]} />
        <h3 id={headingId} className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
          <Link to={`/podcasts/${podcast.slug || podcast.id}`} className="hover:underline">
            {podcast.title}
          </Link>
        </h3>
        <span className="shrink-0 text-xs text-muted">
          {count} episode{count === 1 ? '' : 's'}
        </span>
      </header>
      {/* Rows carry their own ``px-4``; pull the list out by the same amount
          so row text lines up with the group header and the hover band
          bleeds past it, as it does under a ``ListGroup``. */}
      <ul className="-mx-4 divide-y divide-gray-100">
        {podcast.episodes.map((episode) => (
          <EpisodeRow key={episode.id} podcast={podcast} episode={episode} />
        ))}
      </ul>
    </section>
  )
}

interface BriefingIndexProps {
  podcasts: BriefingPodcastGroup[]
}

/**
 * The briefing's episode index as artwork rows grouped by show: the show's
 * artwork and name head each group, and every episode is an inbox-style
 * row with its artwork, title, gist, date and length. Replaces the
 * text-only script rendering on the briefing page; the script stays as
 * the fallback.
 */
export default function BriefingIndex({ podcasts }: BriefingIndexProps) {
  if (podcasts.length === 0) return null
  const episodeCount = podcasts.reduce((sum, podcast) => sum + podcast.episodes.length, 0)
  return (
    <section aria-labelledby="briefing-index-heading" className="space-y-6">
      <h2 id="briefing-index-heading" className="text-section text-ink">
        In this briefing
        <span className="ml-2 text-sm font-normal text-muted">
          {episodeCount} episode{episodeCount === 1 ? '' : 's'} from {podcasts.length} show
          {podcasts.length === 1 ? '' : 's'}
        </span>
      </h2>
      {podcasts.map((podcast) => (
        <PodcastGroup key={podcast.id} podcast={podcast} />
      ))}
    </section>
  )
}

/** Placeholder with the same silhouette as two rows under a group header. */
export function BriefingIndexSkeleton() {
  return (
    <div className="animate-pulse space-y-4" aria-hidden="true">
      <div className="h-5 w-40 rounded bg-gray-200" />
      <div className="flex items-center gap-2.5">
        <div className={`${artworkFrameClass('inline')} bg-gray-200`} />
        <div className="h-4 w-32 rounded bg-gray-200" />
      </div>
      {[0, 1].map((row) => (
        <div key={row} className="flex items-start gap-3 py-2">
          <div className={`${artworkFrameClass('sheet')} bg-gray-200`} />
          <div className="flex-1 space-y-2">
            <div className="h-5 w-3/4 rounded bg-gray-200" />
            <div className="h-4 w-full rounded bg-gray-100" />
            <div className="h-4 w-2/3 rounded bg-gray-100" />
            <div className="h-3 w-1/3 rounded bg-gray-100" />
          </div>
        </div>
      ))}
    </div>
  )
}
