import { Link, useLocation } from 'react-router-dom'
import type { BriefingEpisode, BriefingPodcastGroup } from '../api/types'
import { formatEyebrowDate, formatLength } from '../utils/episodeFormat'
import Artwork from './Artwork'
import { artworkFrameClass } from './artworkRoles'

// The title carries the row's link and stretches it over the whole card with
// an ``::after`` overlay (the ``ListRow`` technique), so each card is one
// real ``<a>``: cmd-click and open-in-new-tab work, no nested interactives.
const STRETCHED_LINK =
  'after:absolute after:inset-0 after:content-[""] after:rounded-lg focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-primary-500'

/** ``58 min`` / ``1 h 10 min`` — whole minutes; seconds are noise on a card. */
function formatCardLength(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null
  return formatLength(Math.max(1, Math.round(seconds / 60)) * 60)
}

interface EpisodeCardProps {
  podcast: BriefingPodcastGroup
  episode: BriefingEpisode
}

function EpisodeCard({ podcast, episode }: EpisodeCardProps) {
  // Spec #52 — carrying the page location in navigation state opens the
  // episode in the reader overlay above the still-mounted briefing.
  const location = useLocation()
  const href = `/podcasts/${podcast.slug || podcast.id}/episodes/${episode.slug || episode.id}`
  const meta = [formatEyebrowDate(episode.pub_date), formatCardLength(episode.duration)].filter(Boolean)

  return (
    <li className="group relative flex items-start gap-4 py-4">
      <div className="min-w-0 flex-1">
        <h4 className="text-base font-semibold leading-snug text-ink sm:text-lg">
          <Link
            to={href}
            state={{ backgroundLocation: location }}
            className={`line-clamp-3 group-hover:text-primary-600 ${STRETCHED_LINK}`}
          >
            {episode.title}
          </Link>
        </h4>
        {episode.summary_preview && (
          <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-muted sm:line-clamp-3">
            {episode.summary_preview}
          </p>
        )}
        {meta.length > 0 && (
          <p className="mt-2 text-xs text-muted">{meta.join(' · ')}</p>
        )}
      </div>
      {/* Episode artwork first (matches the reader header), podcast artwork
          as the fallback when the feed item carries none. */}
      <Artwork role="card" sources={[episode.image_url, podcast.image_url]} />
    </li>
  )
}

function PodcastGroup({ podcast }: { podcast: BriefingPodcastGroup }) {
  const headingId = `briefing-podcast-${podcast.id}`
  const count = podcast.episodes.length
  return (
    <section aria-labelledby={headingId}>
      {/* The "publisher" line: small show artwork and name above its stories. */}
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
      <ul className="divide-y divide-gray-100">
        {podcast.episodes.map((episode) => (
          <EpisodeCard key={episode.id} podcast={podcast} episode={episode} />
        ))}
      </ul>
    </section>
  )
}

interface BriefingIndexProps {
  podcasts: BriefingPodcastGroup[]
}

/**
 * The briefing's episode index as artwork cards grouped by show: the show's
 * artwork and name head each group, and every episode is a card with its
 * title, gist, date and length beside its artwork. Replaces the text-only
 * script rendering on the briefing page; the script stays as the fallback.
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

/** Placeholder with the same silhouette as two cards under a group header. */
export function BriefingIndexSkeleton() {
  return (
    <div className="animate-pulse space-y-4" aria-hidden="true">
      <div className="h-5 w-40 rounded bg-gray-200" />
      <div className="flex items-center gap-2.5">
        <div className={`${artworkFrameClass('inline')} bg-gray-200`} />
        <div className="h-4 w-32 rounded bg-gray-200" />
      </div>
      {[0, 1].map((row) => (
        <div key={row} className="flex items-start gap-4 py-2">
          <div className="flex-1 space-y-2">
            <div className="h-5 w-3/4 rounded bg-gray-200" />
            <div className="h-4 w-full rounded bg-gray-100" />
            <div className="h-4 w-2/3 rounded bg-gray-100" />
            <div className="h-3 w-1/3 rounded bg-gray-100" />
          </div>
          <div className={`${artworkFrameClass('card')} bg-gray-200`} />
        </div>
      ))}
    </div>
  )
}
