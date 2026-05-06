import type { EpisodeEntity } from '../../api/types'
import { entityStyle } from '../../utils/entityColors'

// Spec #28 §5.2 — "Inline entity filter bar (top of transcript view):
// Multi-select chip bar above the transcript: 'Show only segments
// mentioning …'. Selecting one or more entities collapses the
// transcript to segments containing any of them. Pure client-side
// filter — entity_mentions already carries segment_id, so no new
// endpoint needed."

export interface EntityFilterBarProps {
  entities: EpisodeEntity[]
  selectedEntityIds: Set<string>
  onToggle: (entityId: string) => void
  onClear: () => void
}

export default function EntityFilterBar({
  entities,
  selectedEntityIds,
  onToggle,
  onClear,
}: EntityFilterBarProps) {
  // Cap to top-12 by mention count so we don't render dozens of chips
  // for an entity-heavy episode. Selected entities always render even
  // if they fall outside the top-12 — losing your selection because
  // you scrolled the chip list would be surprising.
  const top = entities.slice().sort((a, b) => b.mention_count - a.mention_count).slice(0, 12)
  const extras = entities.filter(
    (e) => selectedEntityIds.has(e.entity.id) && !top.some((t) => t.entity.id === e.entity.id),
  )
  const visible = [...top, ...extras]

  if (entities.length === 0) {
    return null
  }

  const hasSelection = selectedEntityIds.size > 0

  return (
    <div
      role="group"
      aria-label="Filter transcript by entity"
      className="flex flex-wrap items-center gap-1.5"
      data-testid="entity-filter-bar"
    >
      <span className="text-[10px] uppercase tracking-wide text-gray-500">
        {hasSelection ? 'Showing only:' : 'Filter by entity'}
      </span>
      {visible.map((item) => {
        const style = entityStyle(item.entity.type)
        const isSelected = selectedEntityIds.has(item.entity.id)
        return (
          <button
            key={item.entity.id}
            type="button"
            aria-pressed={isSelected}
            onClick={() => onToggle(item.entity.id)}
            className={
              'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ' +
              (isSelected
                ? `${style.pillBg} ${style.pillText} ${style.pillBorder}`
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50')
            }
          >
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
            {item.entity.canonical_name}
            <span className="opacity-70">{item.mention_count}×</span>
          </button>
        )
      })}
      {hasSelection && (
        <button
          type="button"
          onClick={onClear}
          className="ml-1 text-[11px] text-gray-500 hover:text-gray-800 hover:underline"
        >
          Clear
        </button>
      )}
    </div>
  )
}
