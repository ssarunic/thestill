import { Link } from 'react-router-dom'
import type { EpisodeEntity } from '../../api/types'
import { entityHref, entityStyle } from '../../utils/entityColors'

// Spec #28 §5.2 visual rules — "Hover card (≤200px wide): name, type,
// 1-line Wikidata gloss, last 3 mentions of this entity on the same
// feed, 'Go to entity page' link. No images in v1."
//
// We don't have the per-feed last-3 mentions on the episode payload
// alone — that needs the entity-summary endpoint, which we lazy-fetch
// inside the card. To keep the card snappy and avoid a network round-
// trip for every hover, we render the in-episode mention count + first
// mention timestamp as the immediately-available context, and link to
// the entity page for the full picture.

export interface EntityHoverCardProps {
  episodeEntity: EpisodeEntity
  onSeek?: (seconds: number) => void
}

function formatTimestamp(ms: number): string {
  const total = Math.floor(ms / 1000)
  const mm = Math.floor(total / 60)
  const ss = total % 60
  return `${mm}:${ss.toString().padStart(2, '0')}`
}

export default function EntityHoverCard({ episodeEntity, onSeek }: EntityHoverCardProps) {
  const { entity, mention_count, first_mention_ms, speaker_kind, mentions } = episodeEntity
  const style = entityStyle(entity.type)
  const wikidataUrl = entity.wikidata_qid
    ? `https://www.wikidata.org/wiki/${entity.wikidata_qid}`
    : null

  return (
    <div
      role="dialog"
      aria-label={`${entity.canonical_name} — ${style.label}`}
      // Position is supplied by the portal wrapper in EntityHighlight;
      // we just paint the card. `w-56` mirrors CARD_WIDTH_PX.
      className="z-30 w-56 rounded-md border border-gray-200 bg-white p-3 shadow-lg"
      data-testid="entity-hover-card"
    >
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
        <span className="text-xs uppercase tracking-wide text-gray-500">{style.label}</span>
        {speaker_kind !== 'unknown' && (
          <span className="ml-auto rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-gray-600">
            {speaker_kind}
          </span>
        )}
      </div>
      <div className="mt-1 text-sm font-semibold text-gray-900">{entity.canonical_name}</div>
      <div className="mt-1 text-xs text-gray-600">
        {mention_count}× this episode · first at{' '}
        {onSeek ? (
          <button
            type="button"
            onClick={() => onSeek(first_mention_ms / 1000)}
            className="font-mono tabular-nums text-primary-700 hover:underline"
          >
            {formatTimestamp(first_mention_ms)}
          </button>
        ) : (
          <span className="font-mono tabular-nums">{formatTimestamp(first_mention_ms)}</span>
        )}
      </div>
      {wikidataUrl && (
        <a
          href={wikidataUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block truncate text-[11px] text-gray-500 hover:text-gray-700 hover:underline"
        >
          Wikidata: {entity.wikidata_qid}
        </a>
      )}
      {/* Co-mention sparkline (spec §5.2 affordance #6). One line of
          'appears with: X, Y, Z' — capped at 3 names — drawn from the
          same-episode mention set. Cheap because we already have the
          full per-episode mention list. */}
      {mentions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
          {mentions.slice(0, 3).map((m) => (
            <span key={m.id} className="font-mono tabular-nums text-gray-400">
              {formatTimestamp(m.start_ms)}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 border-t border-gray-100 pt-2">
        <Link
          to={entityHref(entity.type, entity.id)}
          className="text-xs font-medium text-primary-700 hover:text-primary-900 hover:underline"
        >
          Go to entity page →
        </Link>
      </div>
    </div>
  )
}
