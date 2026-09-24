import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { EntityCitationRow, EpisodeEntity, MentionLite } from '../../api/types'
import { entityHref, entitySlug, entityStyle } from '../../utils/entityColors'
import { formatClock } from '../../utils/formatClock'
import { useEntitySummary } from '../../hooks/useApi'
import { episodeTimestampPath } from '../../hooks/useDeepLinkSeek'
import { mentionPermalinkHash } from './mentionPermalink'

// Spec #28 §5.2 visual rules — "Hover card (≤200px wide): name, type,
// 1-line Wikidata gloss, last 3 mentions of this entity on the same
// feed, 'Go to entity page' link. No images in v1."
//
// This is the *peek*: everything the reader needs to decide whether the
// entity is worth leaving the transcript for. It renders in two shells
// (see EntityHighlight): a floating card next to the mention on desktop
// and a bottom sheet on phones. Both keep the reader exactly where it
// was; only "Open entity page" navigates.
//
// The gloss and the recent-mention list come from the entity-summary
// endpoint, cached by react-query. The shell decides when the fetch is
// worth it (`summaryEnabled`): on a click, or once a hover has settled —
// a cursor sweeping across a dense paragraph must not fire a summary
// query per entity it crosses.

export interface EntityHoverCardProps {
  episodeEntity: EpisodeEntity
  // The mention the peek was opened from; drives the "n of N" position
  // and the prev/next controls.
  mention: MentionLite
  // The episode being read — its rows are dropped from "Also mentioned on".
  episodeId?: string | null
  onSeek?: (seconds: number) => void
  // Scroll the transcript to another mention of the same entity
  // (without touching playback) — the shell closes the peek afterwards.
  onJumpToMention?: (mention: MentionLite) => void
  // Fired when the user leaves for the entity page, so the shell can
  // close before the route changes.
  onNavigate?: () => void
  // Phone sheet: larger type and tap targets.
  sheet?: boolean
  // Whether to fetch the entity summary (gloss + other episodes) now.
  summaryEnabled?: boolean
}

function formatTimestamp(ms: number): string {
  return formatClock(ms / 1000)
}

const OTHER_MENTIONS_CAP = 3

function otherEpisodeHref(row: EntityCitationRow): string | null {
  if (!row.podcast_slug || !row.episode_slug) return null
  return episodeTimestampPath(row.podcast_slug, row.episode_slug, row.start_ms / 1000)
}

// The mentions prev/next can actually reach: one per segment (a segment's
// mentions of the same entity share one anchor id), and only those whose
// highlight is in the DOM — a mention below the confidence floor, one
// whose surface form was not found in the segment text, or one inside a
// collapsed group has no anchor to scroll to. Sorted by time.
function navigableMentions(entityId: string, mentions: MentionLite[]): MentionLite[] {
  const ordered = mentions.slice().sort((a, b) => a.start_ms - b.start_ms)
  const seenSegments = new Set<number>()
  const out: MentionLite[] = []
  for (const m of ordered) {
    if (seenSegments.has(m.segment_id)) continue
    seenSegments.add(m.segment_id)
    if (typeof document !== 'undefined' && !document.getElementById(mentionPermalinkHash(entityId, m.segment_id))) {
      continue
    }
    out.push(m)
  }
  return out
}

export default function EntityHoverCard({
  episodeEntity,
  mention,
  episodeId = null,
  onSeek,
  onJumpToMention,
  onNavigate,
  sheet = false,
  summaryEnabled = true,
}: EntityHoverCardProps) {
  const { entity, mention_count, speaker_kind, mentions } = episodeEntity
  const style = entityStyle(entity.type)
  const { data: summary } = useEntitySummary(
    summaryEnabled ? entity.type : null,
    summaryEnabled ? entitySlug(entity.id) : null,
  )
  const wikidataUrl = entity.wikidata_qid
    ? `https://www.wikidata.org/wiki/${entity.wikidata_qid}`
    : null

  // In-episode position among the reachable mentions; the current one is
  // located by segment (its own anchor is the one this card opened from).
  // Read once per open — the card only mounts while the peek is showing.
  const ordered = useMemo(() => navigableMentions(entity.id, mentions), [entity.id, mentions])
  const currentIdx = ordered.findIndex((m) => m.segment_id === mention.segment_id)
  const prev = currentIdx > 0 ? ordered[currentIdx - 1] : null
  const next = currentIdx !== -1 && currentIdx < ordered.length - 1 ? ordered[currentIdx + 1] : null

  // "Last 3 mentions on the same feed" (spec) — the summary endpoint
  // returns recent mentions newest-first across every feed. Drop rows
  // from the episode being read (they're the transcript itself) and take
  // the top few; each row says which show it is from so a cross-feed hit
  // is never mistaken for this one.
  const elsewhere = useMemo(() => {
    if (!summary) return []
    const seen = new Set<string>()
    const out: EntityCitationRow[] = []
    for (const row of summary.recent_mentions) {
      if (row.episode_id === episodeId || seen.has(row.episode_id)) continue
      seen.add(row.episode_id)
      out.push(row)
      if (out.length === OTHER_MENTIONS_CAP) break
    }
    return out
  }, [summary, episodeId])

  const textBase = sheet ? 'text-sm' : 'text-xs'
  const textSmall = sheet ? 'text-xs' : 'text-[11px]'
  const tapTarget = sheet ? 'min-h-[44px] px-3' : 'px-1.5 py-0.5'

  return (
    <div
      role={sheet ? undefined : 'dialog'}
      aria-label={sheet ? undefined : `${entity.canonical_name} — ${style.label}`}
      // Position is supplied by the portal wrapper in EntityHighlight;
      // we just paint the card. `w-56` mirrors CARD_WIDTH_PX.
      className={
        sheet
          ? 'px-5 pb-5 pt-1'
          : 'w-56 rounded-md border border-gray-200 bg-white p-3 shadow-lg'
      }
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
      <div className={`mt-1 font-semibold text-gray-900 ${sheet ? 'text-lg' : 'text-sm'}`}>
        {entity.canonical_name}
      </div>
      {summary?.description && (
        <p className={`mt-1 line-clamp-2 text-gray-600 ${textBase}`} data-testid="entity-peek-gloss">
          {summary.description}
        </p>
      )}

      {/* This episode: count, play-from-here, prev/next mention. */}
      <div className={`mt-2 flex items-center gap-2 text-gray-600 ${textBase}`}>
        <span className="flex-1">
          {mention_count}× this episode
          {currentIdx !== -1 && ordered.length > 1 && (
            <span className="text-gray-400"> · {currentIdx + 1} of {ordered.length}</span>
          )}
        </span>
        {onSeek && (
          <button
            type="button"
            onClick={() => onSeek(mention.start_ms / 1000)}
            className={`rounded font-mono tabular-nums text-primary-700 hover:bg-primary-50 hover:underline ${tapTarget}`}
            aria-label={`Play from ${formatTimestamp(mention.start_ms)}`}
          >
            ▶ {formatTimestamp(mention.start_ms)}
          </button>
        )}
      </div>
      {onJumpToMention && ordered.length > 1 && (
        <div className={`mt-1 flex items-center gap-1 ${textBase}`}>
          <button
            type="button"
            disabled={!prev}
            onClick={() => prev && onJumpToMention(prev)}
            className={`flex-1 rounded text-left text-primary-700 hover:bg-primary-50 disabled:text-gray-300 disabled:hover:bg-transparent ${tapTarget}`}
            aria-label={prev ? `Previous mention at ${formatTimestamp(prev.start_ms)}` : 'No previous mention'}
          >
            ← Prev{prev && <span className="ml-1 font-mono tabular-nums text-gray-400">{formatTimestamp(prev.start_ms)}</span>}
          </button>
          <button
            type="button"
            disabled={!next}
            onClick={() => next && onJumpToMention(next)}
            className={`flex-1 rounded text-right text-primary-700 hover:bg-primary-50 disabled:text-gray-300 disabled:hover:bg-transparent ${tapTarget}`}
            aria-label={next ? `Next mention at ${formatTimestamp(next.start_ms)}` : 'No next mention'}
          >
            {next && <span className="mr-1 font-mono tabular-nums text-gray-400">{formatTimestamp(next.start_ms)}</span>}Next →
          </button>
        </div>
      )}

      {/* Elsewhere: the last few episodes (any feed) that mention this
          entity, straight from the summary endpoint. */}
      {elsewhere.length > 0 && (
        <div className="mt-2 border-t border-gray-100 pt-2" data-testid="entity-peek-elsewhere">
          <div className={`uppercase tracking-wide text-gray-400 ${textSmall}`}>Also mentioned on</div>
          <ul className="mt-1 space-y-1">
            {elsewhere.map((row) => {
              const href = otherEpisodeHref(row)
              const label = (
                <>
                  <span className="font-medium text-gray-700">{row.podcast_title}</span>
                  <span className="text-gray-500"> · {row.episode_title}</span>{' '}
                  <span className="font-mono tabular-nums text-gray-400">{formatTimestamp(row.start_ms)}</span>
                </>
              )
              return (
                <li key={`${row.episode_id}-${row.start_ms}`} className={`truncate ${textBase}`}>
                  {href ? (
                    <Link to={href} onClick={onNavigate} className="hover:underline">
                      {label}
                    </Link>
                  ) : (
                    label
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <div className={`mt-2 flex items-center justify-between gap-2 border-t border-gray-100 pt-2 ${textBase}`}>
        <Link
          to={entityHref(entity.type, entity.id)}
          onClick={onNavigate}
          className={`rounded font-medium text-primary-700 hover:text-primary-900 hover:underline ${sheet ? 'flex min-h-[44px] items-center' : ''}`}
        >
          Open entity page →
        </Link>
        {wikidataUrl && (
          <a
            href={wikidataUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={`truncate text-gray-500 hover:text-gray-700 hover:underline ${textSmall}`}
          >
            Wikidata
          </a>
        )}
      </div>
    </div>
  )
}
