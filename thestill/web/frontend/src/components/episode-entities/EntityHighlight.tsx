import { useState, useRef, useEffect, useCallback, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import { entityHref, entityStyle } from '../../utils/entityColors'
import EntityHoverCard from './EntityHoverCard'

// Spec #28 §5.2 affordance #14 — `#m=<entity_id>:<segment_id>` hash
// permalinks. Built into the inline highlight so MCP tools / shared
// links can deep-anchor a specific mention.
export function mentionPermalinkHash(entityId: string, segmentId: number): string {
  return `m=${entityId}:${segmentId}`
}

export interface EntityHighlightProps {
  episodeEntity: EpisodeEntity
  mention: MentionLite
  // The text the highlight wraps. Provided by the segment renderer
  // because it may itself be a partial slice of `segment.text` (e.g.
  // when a search highlight is interleaved).
  children: ReactNode
  onSeek?: (seconds: number) => void
  // Notifies the parent which entity the user last hovered, so the
  // `[`/`]` keyboard nav (affordance #1) can jump between mentions of
  // the focused entity.
  onFocusEntity?: (entityId: string) => void
}

// Card width must match `EntityHoverCard`'s `w-56` (14rem ≈ 224px) so
// the right-edge flip math knows when to pivot. If the card width
// changes, update this constant.
const CARD_WIDTH_PX = 224
const CARD_GAP_PX = 4

interface CardPosition {
  top: number
  left: number
}

export default function EntityHighlight({
  episodeEntity,
  mention,
  children,
  onSeek,
  onFocusEntity,
}: EntityHighlightProps) {
  const { entity } = episodeEntity
  const style = entityStyle(entity.type)
  const [hoverOpen, setHoverOpen] = useState(false)
  const [cardPosition, setCardPosition] = useState<CardPosition | null>(null)
  const linkRef = useRef<HTMLAnchorElement | null>(null)
  // Small delay on close so the user can move the cursor from the
  // link into the card without it disappearing under them.
  const closeTimer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    }
  }, [])

  const computePosition = useCallback((): CardPosition | null => {
    const link = linkRef.current
    if (!link) return null
    const rect = link.getBoundingClientRect()
    // Default: align to the link's left edge, just below it. If that
    // would overflow the viewport's right edge, flip to right-align
    // against the link instead. This avoids the card being clipped at
    // the right edge of a narrow transcript column.
    const viewportWidth = window.innerWidth
    let left = rect.left
    if (left + CARD_WIDTH_PX > viewportWidth - 8) {
      left = Math.max(8, rect.right - CARD_WIDTH_PX)
    }
    // Add window scroll offsets so the absolute position lands in
    // document coordinates (the portal renders into <body>).
    return {
      top: rect.bottom + window.scrollY + CARD_GAP_PX,
      left: left + window.scrollX,
    }
  }, [])

  const open = useCallback(() => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
    setCardPosition(computePosition())
    setHoverOpen(true)
    onFocusEntity?.(entity.id)
  }, [computePosition, entity.id, onFocusEntity])
  const scheduleClose = useCallback(() => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setHoverOpen(false), 150)
  }, [])

  const ariaLabel = `${entity.canonical_name}, ${style.label}, ${
    episodeEntity.mention_count
  } mention${episodeEntity.mention_count === 1 ? '' : 's'}`

  return (
    <>
      <a
        ref={linkRef}
        href={entityHref(entity.type, entity.id)}
        id={mentionPermalinkHash(entity.id, mention.segment_id)}
        aria-label={ariaLabel}
        data-entity-id={entity.id}
        data-entity-type={entity.type}
        data-mention-id={mention.id}
        // Plain anchor so right-click "open in new tab" works (spec
        // affordance #20 a11y baseline). Stop the click bubbling so it
        // doesn't trigger the seekable wrapping div.
        onClick={(e) => e.stopPropagation()}
        onMouseEnter={open}
        onMouseLeave={scheduleClose}
        onFocus={open}
        onBlur={scheduleClose}
        className={`underline ${style.inlineUnderline} hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded-sm`}
      >
        {children}
      </a>
      {hoverOpen && cardPosition && typeof document !== 'undefined'
        ? createPortal(
            <span
              // Portal-rendered so ancestor `overflow:hidden` can't
              // clip the card, and so we can flip horizontally near
              // the viewport's right edge.
              onMouseEnter={open}
              onMouseLeave={scheduleClose}
              style={{
                position: 'absolute',
                top: cardPosition.top,
                left: cardPosition.left,
                width: CARD_WIDTH_PX,
              }}
            >
              <EntityHoverCard episodeEntity={episodeEntity} onSeek={onSeek} />
            </span>,
            document.body,
          )
        : null}
    </>
  )
}
