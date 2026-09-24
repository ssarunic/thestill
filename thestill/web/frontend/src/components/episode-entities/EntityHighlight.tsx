import { useState, useRef, useEffect, useCallback, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import { entityHref, entityStyle } from '../../utils/entityColors'
import { useIsSmUp } from '../../hooks/useMediaQuery'
import EntityHoverCard from './EntityHoverCard'
import EntityPeekSheet from './EntityPeekSheet'

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
  // The episode being read; lets the peek skip its own episode when
  // listing where else the entity comes up.
  episodeId?: string | null
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

// A plain left click. Modifier clicks and middle clicks are left to the
// browser so "open in new tab" keeps working on the underlying anchor.
function isPlainClick(e: MouseEvent): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
}

/**
 * An inline entity mention in the transcript.
 *
 * The mention never navigates on a plain click: it opens a *peek*
 * (EntityHoverCard) that keeps the reader in place — a floating card next
 * to the word on desktop (also shown on hover), a bottom sheet on phones.
 * Only the peek's "Open entity page" link leaves the transcript, and it
 * is a router link, so Back returns to the reader — overlay state and
 * scroll position included. The `href` stays on the anchor for
 * modifier-click / middle-click / right-click → new tab.
 */
export default function EntityHighlight({
  episodeEntity,
  mention,
  children,
  episodeId = null,
  onSeek,
  onFocusEntity,
}: EntityHighlightProps) {
  const { entity } = episodeEntity
  const style = entityStyle(entity.type)
  const isSmUp = useIsSmUp()
  const [hoverOpen, setHoverOpen] = useState(false)
  // Pinned by a click: survives mouse-out; closed by Esc, a click
  // outside, scrolling, a jump, or navigating away.
  const [pinned, setPinned] = useState(false)
  const [cardPosition, setCardPosition] = useState<CardPosition | null>(null)
  const linkRef = useRef<HTMLAnchorElement | null>(null)
  const cardRef = useRef<HTMLSpanElement | null>(null)
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

  const cancelClose = useCallback(() => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }, [])
  const closeAll = useCallback(() => {
    cancelClose()
    setPinned(false)
    setHoverOpen(false)
  }, [cancelClose])

  // Hover/focus peek — desktop only. On a touch screen the synthetic
  // mouseenter a tap fires would flash the card under the finger before
  // the click opened the sheet.
  const open = useCallback(() => {
    if (!isSmUp) return
    cancelClose()
    setCardPosition(computePosition())
    setHoverOpen(true)
    onFocusEntity?.(entity.id)
  }, [isSmUp, cancelClose, computePosition, entity.id, onFocusEntity])
  const scheduleClose = useCallback(() => {
    if (pinned) return
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setHoverOpen(false), 150)
  }, [pinned])

  const onClick = useCallback(
    (e: MouseEvent<HTMLAnchorElement>) => {
      // Stop the click bubbling so it doesn't trigger the seekable
      // wrapping div.
      e.stopPropagation()
      if (!isPlainClick(e)) return
      e.preventDefault()
      onFocusEntity?.(entity.id)
      if (pinned) {
        closeAll()
        return
      }
      cancelClose()
      setCardPosition(computePosition())
      setPinned(true)
      setHoverOpen(true)
    },
    [pinned, closeAll, cancelClose, computePosition, entity.id, onFocusEntity],
  )

  // While pinned on desktop: Esc, a click outside the link/card, or any
  // scroll closes it (the card is positioned in document coordinates and
  // would drift away from its word when an inner container scrolls).
  useEffect(() => {
    if (!pinned || !isSmUp) return
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // Claim the key so the reader overlay / Now Playing sheet (which
      // check `defaultPrevented`) don't also close on the same press.
      e.preventDefault()
      closeAll()
    }
    function onPointerDown(e: globalThis.MouseEvent) {
      const target = e.target as Node | null
      if (linkRef.current?.contains(target) || cardRef.current?.contains(target)) return
      closeAll()
    }
    function onScroll(e: Event) {
      if (cardRef.current?.contains(e.target as Node | null)) return
      closeAll()
    }
    window.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('scroll', onScroll, { capture: true, passive: true })
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('scroll', onScroll, { capture: true })
    }
  }, [pinned, isSmUp, closeAll])

  // Prev/next mention from the peek: scroll the transcript to that
  // mention's own highlight (every mention carries a permalink id) and
  // close. Playback is untouched — the ▶ button is the seek path. The
  // target is deliberately not focused: its focus handler would open a
  // hover card mid-scroll. `onFocusEntity` keeps `[`/`]` on this entity.
  const jumpToMention = useCallback(
    (target: MentionLite) => {
      closeAll()
      onFocusEntity?.(entity.id)
      const node = document.getElementById(mentionPermalinkHash(entity.id, target.segment_id))
      if (!node) return
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      node.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
    },
    [closeAll, entity.id, onFocusEntity],
  )

  const ariaLabel = `${entity.canonical_name}, ${style.label}, ${
    episodeEntity.mention_count
  } mention${episodeEntity.mention_count === 1 ? '' : 's'}`

  const showCard = isSmUp && (hoverOpen || pinned) && cardPosition && typeof document !== 'undefined'
  const showSheet = !isSmUp && pinned

  return (
    <>
      <a
        ref={linkRef}
        href={entityHref(entity.type, entity.id)}
        id={mentionPermalinkHash(entity.id, mention.segment_id)}
        aria-label={ariaLabel}
        aria-expanded={hoverOpen || pinned}
        data-entity-id={entity.id}
        data-entity-type={entity.type}
        data-mention-id={mention.id}
        onClick={onClick}
        onMouseEnter={open}
        onMouseLeave={scheduleClose}
        onFocus={open}
        onBlur={scheduleClose}
        className={`underline ${style.inlineUnderline} hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded-sm ${
          pinned ? 'bg-gray-100' : ''
        }`}
      >
        {children}
      </a>
      {showCard
        ? createPortal(
            <span
              ref={cardRef}
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
                zIndex: 30,
              }}
            >
              <EntityHoverCard
                episodeEntity={episodeEntity}
                mention={mention}
                episodeId={episodeId}
                onSeek={onSeek}
                onJumpToMention={jumpToMention}
                onNavigate={closeAll}
              />
            </span>,
            document.body,
          )
        : null}
      {showSheet && (
        <EntityPeekSheet label={`${entity.canonical_name} — ${style.label}`} onClose={closeAll}>
          <EntityHoverCard
            episodeEntity={episodeEntity}
            mention={mention}
            episodeId={episodeId}
            onSeek={onSeek}
            onJumpToMention={jumpToMention}
            onNavigate={closeAll}
            sheet
          />
        </EntityPeekSheet>
      )}
    </>
  )
}
