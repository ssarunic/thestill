import { Link, useLocation } from 'react-router-dom'
import type { ResumableItem } from '../api/types'
import { usePlayer } from '../contexts/PlayerContext'
import { listeningCache } from '../contexts/listening/listeningCache'
import { useInProgressListening } from '../hooks/useListening'
import { progressFraction, timeLeftLabel } from '../utils/listeningFormat'
import { buildPlayerTrack } from '../utils/playerTrack'
import { PlayIcon } from './Button'
import { ArtworkWithProgress } from './ListeningProgress'
import { ListRowArtwork } from './ListRow'

// The strip shows a handful; the full list is a later listening-history page.
const LIMIT = 3

function ContinueItem({ item }: { item: ResumableItem }) {
  const player = usePlayer()
  // Carrying the inbox location opens the episode in the reader overlay, as
  // an inbox row does; Cmd/middle-click opens the standalone page.
  const location = useLocation()
  const { episode, listening } = item
  const left = timeLeftLabel(listening, episode.duration)
  const episodeHref = `/podcasts/${episode.podcast_slug || episode.podcast_id}/episodes/${episode.slug || episode.id}`
  return (
    <li className="flex w-72 shrink-0 snap-start items-center rounded-lg border border-gray-200 bg-white">
      <Link
        to={episodeHref}
        state={{ backgroundLocation: location }}
        className="flex min-w-0 flex-1 items-center gap-3 rounded-l-lg py-2 pl-2 hover:bg-gray-50"
      >
        <ArtworkWithProgress
          artwork={<ListRowArtwork sources={[episode.image_url, episode.podcast_image_url]} />}
          fraction={progressFraction(listening, episode.duration)}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-gray-500">
            {episode.podcast_title}
            {left && ` · ${left}`}
          </span>
          <span className="line-clamp-2 text-sm leading-snug text-gray-700">{episode.title}</span>
        </span>
      </Link>
      <button
        type="button"
        aria-label={`Resume ${episode.title}${left ? `, ${left}` : ''}`}
        onClick={() => {
          // Resume in place: the cache must know the position before play()
          // reads it synchronously inside this click.
          listeningCache.seed(episode.id, listening)
          player.play(buildPlayerTrack(episode))
        }}
        className="mx-1 h-10 w-10 shrink-0 rounded-full p-3 text-gray-500 hover:bg-gray-100 hover:text-primary-600"
      >
        <PlayIcon />
      </button>
    </li>
  )
}

/**
 * Spec #90 — episodes you are in the middle of, above the inbox list: one
 * row of cards that scrolls sideways, so the strip costs one row's height
 * on a phone. Each card opens the episode; its play button resumes in
 * place. Hidden when there are none, and on a failed request (absent, not a
 * fake "nothing in progress").
 */
export default function ContinueListening() {
  const { data, isError } = useInProgressListening(LIMIT)
  if (isError || !data || data.length === 0) return null
  return (
    <section aria-labelledby="continue-listening-heading" data-testid="continue-listening">
      <h2 id="continue-listening-heading" className="mb-2 text-sm font-medium text-gray-500">
        Continue listening
      </h2>
      <ul className="-mx-4 flex snap-x snap-mandatory scroll-px-4 gap-2 overflow-x-auto px-4 [scrollbar-width:none] sm:mx-0 sm:px-0 [&::-webkit-scrollbar]:hidden">
        {data.map((item) => (
          <ContinueItem key={item.episode.id} item={item} />
        ))}
      </ul>
    </section>
  )
}
