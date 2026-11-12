import { selectTopEntities } from '../../utils/mentionDensity'
import type { EpisodeEntity, EntityType } from '../../api/types'
import { entityStyle } from '../../utils/entityColors'
import { useIsSmUp } from '../../hooks/useMediaQuery'
import EntityHighlight from './EntityHighlight'
import EntityTypeToggles from './EntityTypeToggles'
import { NO_SEGMENT, synthesizeIndexMention } from './indexMention'

// Spec #28 §5.2 — "Episode header 'key entities' strip (above the
// fold, mobile-first): horizontal strip rendered between the episode
// header card and the transcript/summary tabs. Shows the top 5
// entities by mention count (any type), color-coded by type using the
// same four-swatch palette as inline rendering."
//
// Plus affordance #3 — type filter toggles (P / C / Pr / T) let the
// reader hide whole categories without losing per-entity state.
//
// Shown below `lg` only: from `lg` up the rail beside the text lists the
// same entities and more, and carries the type toggles, so the reader
// does not render the strip there.
//
// A pill opens the same *peek* as a transcript mention (hover card on
// desktop, bottom sheet on a phone) instead of leaving for the entity
// page; the peek's name link is the way there, and "Show in transcript"
// jumps to the first mention. The `href` stays on the anchor for
// modifier / middle clicks.

export interface KeyEntitiesStripProps {
  entities: EpisodeEntity[]
  hiddenTypes: Set<EntityType>
  onToggleType: (type: EntityType) => void
  topN?: number
  // The episode being read; the peek skips it when listing where else
  // the entity comes up.
  episodeId?: string | null
  onSeek?: (seconds: number) => void
  // Peek action: switch to the transcript and scroll to a segment.
  onShowInTranscript?: (segmentId: number) => void
  onFocusEntity?: (entityId: string) => void
}

export default function KeyEntitiesStrip({
  entities,
  hiddenTypes,
  onToggleType,
  topN = 5,
  episodeId = null,
  onSeek,
  onShowInTranscript,
  onFocusEntity,
}: KeyEntitiesStripProps) {
  const isSmUp = useIsSmUp()
  const visible = selectTopEntities(
    entities.filter((e) => !hiddenTypes.has(e.entity.type)),
    topN,
  )

  if (entities.length === 0) {
    // Spec §5.3 empty state: episode with 0 resolved entities hides
    // the strip entirely. Render nothing.
    return null
  }

  return (
    <div
      role="group"
      aria-label="Key entities in this episode"
      className="flex flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-gray-50/60 px-3 py-2"
      data-testid="key-entities-strip"
    >
      <span className="text-[10px] uppercase tracking-wide text-gray-500">Key entities</span>
      {visible.map((item) => {
        const style = entityStyle(item.entity.type)
        const mention = synthesizeIndexMention(item)
        return (
          <EntityHighlight
            key={item.entity.id}
            variant="index"
            episodeEntity={item}
            mention={mention}
            episodeId={episodeId}
            isSmUp={isSmUp}
            onSeek={onSeek}
            onShowInTranscript={mention.segment_id === NO_SEGMENT ? undefined : onShowInTranscript}
            onFocusEntity={onFocusEntity}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors hover:brightness-95 ${style.pillBg} ${style.pillText} ${style.pillBorder}`}
          >
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
            {item.entity.canonical_name}
            <span className="opacity-70">{item.mention_count}×</span>
          </EntityHighlight>
        )
      })}
      {visible.length === 0 && (
        <span className="text-xs italic text-gray-400">All entity types hidden — toggle one back on.</span>
      )}
      <EntityTypeToggles hiddenTypes={hiddenTypes} onToggleType={onToggleType} className="ml-auto" />
    </div>
  )
}
