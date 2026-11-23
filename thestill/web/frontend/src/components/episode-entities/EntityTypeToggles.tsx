import type { EntityType } from '../../api/types'
import { ENTITY_STYLES } from '../../utils/entityColors'

// Spec #28 §5.2 affordance #3 — the type toggles (P / C / Pr / T): hide a
// whole type of entity from the highlights in the text without losing
// per-entity state. One control, shown where the episode's entities are
// listed: in the key entities strip below `lg`, in the rail from `lg` up.

const TYPES: EntityType[] = ['person', 'company', 'product', 'topic']

export interface EntityTypeTogglesProps {
  hiddenTypes: Set<EntityType>
  onToggleType: (type: EntityType) => void
  className?: string
}

export default function EntityTypeToggles({ hiddenTypes, onToggleType, className = '' }: EntityTypeTogglesProps) {
  return (
    <span className={`inline-flex items-center gap-1 ${className}`} role="group" aria-label="Filter by entity type">
      {TYPES.map((type) => {
        const style = ENTITY_STYLES[type]
        const isVisible = !hiddenTypes.has(type)
        return (
          <button
            key={type}
            type="button"
            aria-pressed={isVisible}
            aria-label={`${isVisible ? 'Hide' : 'Show'} ${style.label.toLowerCase()}s`}
            onClick={() => onToggleType(type)}
            className={
              'inline-flex items-center justify-center rounded-md border px-1.5 py-0.5 text-[10px] font-mono font-semibold transition-colors ' +
              (isVisible
                ? `${style.pillBg} ${style.pillText} ${style.pillBorder}`
                : 'bg-gray-100 text-gray-400 border-gray-200 line-through')
            }
          >
            {style.shortCode}
          </button>
        )
      })}
    </span>
  )
}
