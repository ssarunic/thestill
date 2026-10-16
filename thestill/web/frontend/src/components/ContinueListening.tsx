import type { ResumableItem } from '../api/types'
import { usePlayer } from '../contexts/PlayerContext'
import { listeningCache } from '../contexts/listening/listeningCache'
import { useInProgressListening } from '../hooks/useListening'
import { progressFraction, timeLeftLabel } from '../utils/listeningFormat'
import { buildPlayerTrack } from '../utils/playerTrack'
import { ArtworkWithProgress } from './ListeningProgress'
import { ListRowArtwork } from './ListRow'

// The strip shows a handful; the full list is a later listening-history page.
const LIMIT = 3

function ContinueItem({ item }: { item: ResumableItem }) {
  const player = usePlayer()
  const { episode, listening } = item
  const left = timeLeftLabel(listening, episode.duration)
  return (
    <li>
      <button
        type="button"
        aria-label={`Resume ${episode.title}${left ? `, ${left}` : ''}`}
        onClick={() => {
          // Resume in place: the cache must know the position before play()
          // reads it synchronously inside this click.
          listeningCache.seed(episode.id, listening)
          player.play(buildPlayerTrack(episode))
        }}
        className="flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-gray-50"
      >
        <ArtworkWithProgress
          artwork={<ListRowArtwork sources={[episode.image_url, episode.podcast_image_url]} />}
          fraction={progressFraction(listening, episode.duration)}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-gray-500">{episode.podcast_title}</span>
          <span className="block truncate text-sm text-gray-700">{episode.title}</span>
        </span>
        {left && <span className="shrink-0 whitespace-nowrap text-xs text-gray-500">{left}</span>}
      </button>
    </li>
  )
}

/**
 * Spec #90 — episodes you are in the middle of, above the inbox list. Each
 * item resumes in place. Hidden when there are none, and on a failed
 * request (absent, not a fake "nothing in progress").
 */
export default function ContinueListening() {
  const { data, isError } = useInProgressListening(LIMIT)
  if (isError || !data || data.length === 0) return null
  return (
    <section aria-labelledby="continue-listening-heading" data-testid="continue-listening">
      <h2 id="continue-listening-heading" className="mb-2 text-sm font-medium text-gray-500">
        Continue listening
      </h2>
      <ul className="space-y-1">
        {data.map((item) => (
          <ContinueItem key={item.episode.id} item={item} />
        ))}
      </ul>
    </section>
  )
}
