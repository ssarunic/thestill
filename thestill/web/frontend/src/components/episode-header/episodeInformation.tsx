import { Link } from 'react-router-dom'
import type { EpisodeDetail, ImportKind } from '../../api/types'
import type { DefinitionRow } from '../DefinitionList'
import { ExternalLink } from '../ExternalLink'
import { episodeTypeLabel, formatLength, formatPublished, hostOf, languageName } from '../../utils/episodeFormat'

const IMPORT_KIND_LABEL: Record<ImportKind, string> = {
  bare_audio: 'Imported (audio file)',
  youtube: 'Imported (YouTube)',
  apple_episode: 'Imported (Apple Podcasts)',
  rss_episode: 'Imported (RSS episode)',
}

function sourceLabel(episode: EpisodeDetail): string | null {
  if (episode.origin !== 'import') return null
  return episode.import_kind ? IMPORT_KIND_LABEL[episode.import_kind] : 'Imported'
}

/**
 * Spec #76 §3.6 — the rows of the Information list. Every fact the old
 * meta line spread across the header lives here, in order; rows with no
 * value are dropped by ``DefinitionList``.
 */
export function buildEpisodeInformationRows(episode: EpisodeDetail): DefinitionRow[] {
  const notesHost = hostOf(episode.website_url)
  return [
    {
      label: 'Show',
      value: (
        <Link to={`/podcasts/${episode.podcast_slug}`} className="text-primary-700 hover:underline">
          {episode.podcast_title}
        </Link>
      ),
    },
    { label: 'Author', value: episode.podcast_author },
    { label: 'Published', value: formatPublished(episode.pub_date), numeric: true },
    { label: 'Length', value: formatLength(episode.duration), numeric: true },
    { label: 'Language', value: languageName(episode.podcast_language) },
    { label: 'Type', value: episodeTypeLabel(episode.episode_type) },
    { label: 'Explicit', value: episode.explicit == null ? null : episode.explicit ? 'Yes' : 'No' },
    {
      label: 'Show notes',
      value:
        episode.website_url && notesHost ? (
          <ExternalLink href={episode.website_url} className="text-sm">
            {notesHost}
          </ExternalLink>
        ) : null,
    },
    { label: 'Source', value: sourceLabel(episode) },
  ]
}
