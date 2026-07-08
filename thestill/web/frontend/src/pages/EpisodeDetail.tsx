import { useParams, Link } from 'react-router-dom'
import { useEpisode } from '../hooks/useApi'
import EpisodeReader from '../components/EpisodeReader'

/**
 * Standalone episode page: breadcrumb + the shared EpisodeReader
 * (spec #52). The reader owns all episode data fetching; the breadcrumb's
 * `useEpisode` call here resolves from the same React Query cache entry,
 * so no extra request is made.
 */
export default function EpisodeDetail() {
  const { podcastSlug, episodeSlug } = useParams<{ podcastSlug: string; episodeSlug: string }>()
  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(podcastSlug!, episodeSlug!)
  const episode = episodeData?.episode

  return (
    <div className="space-y-6">
      {/* Breadcrumb — page-only chrome; the overlay renders `← Inbox`
          instead. Hidden on error: the reader shows the error card. */}
      {!episodeError && (
        <nav className="text-sm flex flex-wrap items-center gap-1">
          <Link to="/podcasts" className="text-gray-500 hover:text-gray-700">Podcasts</Link>
          <span className="text-gray-400">/</span>
          <Link to={`/podcasts/${podcastSlug}`} className="text-gray-500 hover:text-gray-700 truncate max-w-[120px] sm:max-w-none">{episodeLoading ? '...' : episode?.podcast_title}</Link>
          <span className="text-gray-400 hidden sm:inline">/</span>
          <span className="text-gray-900 truncate max-w-[150px] sm:max-w-none hidden sm:inline">{episodeLoading ? '...' : episode?.title}</span>
        </nav>
      )}

      <EpisodeReader />
    </div>
  )
}
