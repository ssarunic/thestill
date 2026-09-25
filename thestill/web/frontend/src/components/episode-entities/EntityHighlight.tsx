import {
  useState,
  useRef,
  useEffect,
  useCallback,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import { entityHref, entityStyle } from '../../utils/entityColors'
import { TRANSIENT_LAYER_Z } from '../../constants/layers'
import EntityHoverCard from './EntityHoverCard'
import EntityPeekSheet from './EntityPeekSheet'

import { findMentionAnchor, isSpeakingMention, mentionPermalinkHash, speakerAnchorId } from './mentionPermalink'

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
  // Desktop (hover card) vs phone (bottom sheet). Resolved once by the
  // transcript viewer and passed down — a long transcript renders
  // hundreds of highlights, one media-query subscription each would be
  // wasteful.
  isSmUp?: boolean
  // `inline` (default): a name inside the segment text, underlined in the
  // entity's colour. `speaker`: the speaker label at the head of a
  // segment — keeps the speaker colour it is given, no underline until
  // hover, and its own anchor id (see `speakerAnchorId`). `index`: an
  // entry in one of the page's entity indexes (the right rail, the key
  // entities strip) — the caller supplies the look via `className`, there
  // is no anchor id (the transcript's anchors must stay unique for
  // `[`/`]` and permalinks), and the peek offers "Show in transcript"
  // instead of prev/next.
  variant?: 'inline' | 'speaker' | 'index'
  // Index variant only: the entry's own classes (a rail row, a pill).
  className?: string
  onSeek?: (seconds: number) => void
  // Index variant: switch to the transcript and scroll to the segment
  // the mention was borrowed from. No seek — the ▶ button is the seek path.
  onShowInTranscript?: (segmentId: number) => void
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
// Mouse-out grace: the cursor can travel from the word into the card.
const HOVER_CLOSE_DELAY_MS = 150
// A hover that outlives a cursor sweep across the paragraph is worth a
// summary round-trip; a shorter one is not.
const HOVER_FETCH_DELAY_MS = 300

interface CardPosition {
  top: number
  left: number
}

// A plain left click. Modifier clicks and middle clicks are left to the
// browser so "open in new tab" keeps working on the underlying anchor.
function isPlainClick(e: MouseEvent): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
}

// The peek is rendered through a portal, but React synthetic events still
// bubble up the *component* tree — straight into the seekable transcript
// segment that owns the highlight. Every click or key inside the peek
// stops here so "Next mention" never seeks playback to the current line.
function stopReactPropagation(e: MouseEvent | ReactKeyboardEvent) {
  e.stopPropagation()
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
  isSmUp = true,
  variant = 'inline',
  className,
  onSeek,
  onShowInTranscript,
  onFocusEntity,
}: EntityHighlightProps) {
  const { entity } = episodeEntity
  const style = entityStyle(entity.type)
  const isSpeaker = variant === 'speaker'
  const isIndex = variant === 'index'
  const [hoverOpen, setHoverOpen] = useState(false)
  // Pinned by a click: survives mouse-out; closed by Esc, a click
  // outside, the word scrolling out of view, a jump, or navigating away.
  const [pinned, setPinned] = useState(false)
  // The hover has lasted long enough to be deliberate — only then does
  // the card fetch the entity summary (a Postgres query per entity).
  const [hoverSettled, setHoverSettled] = useState(false)
  const [cardPosition, setCardPosition] = useState<CardPosition | null>(null)
  const linkRef = useRef<HTMLAnchorElement | null>(null)
  const cardRef = useRef<HTMLSpanElement | null>(null)
  const closeTimer = useRef<number | null>(null)
  const settleTimer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
      if (settleTimer.current !== null) window.clearTimeout(settleTimer.current)
    }
  }, [])

  // Viewport coordinates: the portal is `position: fixed`, so the card
  // tracks its word through any scroll container by recomputing on
  // scroll, rather than being pinned in document space and drifting.
  const computePosition = useCallback((): CardPosition | null => {
    const link = linkRef.current
    if (!link) return null
    const rect = link.getBoundingClientRect()
    // Default: align to the link's left edge, just below it. If that
    // would overflow the viewport's right edge, flip to right-align
    // against the link instead. This avoids the card being clipped at
    // the right edge of a narrow transcript column.
    let left = rect.left
    if (left + CARD_WIDTH_PX > window.innerWidth - 8) {
      left = Math.max(8, rect.right - CARD_WIDTH_PX)
    }
    return { top: rect.bottom + CARD_GAP_PX, left }
  }, [])

  const cancelTimers = useCallback(() => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
    if (settleTimer.current !== null) {
      window.clearTimeout(settleTimer.current)
      settleTimer.current = null
    }
  }, [])
  const closeAll = useCallback(() => {
    cancelTimers()
    setPinned(false)
    setHoverOpen(false)
    setHoverSettled(false)
  }, [cancelTimers])

  // Hover/focus peek — desktop only. On a touch screen the synthetic
  // mouseenter a tap fires would flash the card under the finger before
  // the click opened the sheet.
  const open = useCallback(() => {
    if (!isSmUp) return
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
    if (settleTimer.current === null) {
      settleTimer.current = window.setTimeout(() => {
        settleTimer.current = null
        setHoverSettled(true)
      }, HOVER_FETCH_DELAY_MS)
    }
    setCardPosition(computePosition())
    setHoverOpen(true)
    onFocusEntity?.(entity.id)
  }, [isSmUp, computePosition, entity.id, onFocusEntity])
  const scheduleClose = useCallback(() => {
    if (pinned) return
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null
      if (settleTimer.current !== null) {
        window.clearTimeout(settleTimer.current)
        settleTimer.current = null
      }
      setHoverOpen(false)
      setHoverSettled(false)
    }, HOVER_CLOSE_DELAY_MS)
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
      cancelTimers()
      setCardPosition(computePosition())
      setPinned(true)
      setHoverOpen(true)
    },
    [pinned, closeAll, cancelTimers, computePosition, entity.id, onFocusEntity],
  )

  // While pinned on desktop: Esc or a click outside the link/card closes
  // it. Esc is taken in the capture phase on `document` so it runs before
  // the reader overlay's bubbling `document` listener (which would
  // otherwise close the whole reader on the same press — the overlay's
  // focus guard does not help, the mention sits inside its panel).
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
    document.addEventListener('keydown', onKey, { capture: true })
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKey, { capture: true })
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [pinned, isSmUp, closeAll])

  // While the card is showing: any scroll — the user's, or follow-playback
  // / `[` `]` / deep-link auto-scroll — moves the card with its word. Once
  // the word leaves the viewport the card has nothing to point at and
  // closes.
  const cardShowing = isSmUp && (hoverOpen || pinned)
  useEffect(() => {
    if (!cardShowing) return
    function onScroll(e: Event) {
      if (cardRef.current?.contains(e.target as Node | null)) return
      const link = linkRef.current
      if (!link) return
      const rect = link.getBoundingClientRect()
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        closeAll()
        return
      }
      const next = computePosition()
      setCardPosition((prev) =>
        prev && next && prev.top === next.top && prev.left === next.left ? prev : next,
      )
    }
    document.addEventListener('scroll', onScroll, { capture: true, passive: true })
    return () => document.removeEventListener('scroll', onScroll, { capture: true })
  }, [cardShowing, closeAll, computePosition])

  // Prev/next mention from the peek: scroll the transcript to that
  // mention's own highlight (the card only offers mentions whose anchor is
  // rendered) and close. Playback is untouched — the ▶ button is the seek
  // path. The target is deliberately not focused: its focus handler would
  // open a hover card mid-scroll. `onFocusEntity` keeps `[`/`]` on this
  // entity.
  const jumpToMention = useCallback(
    (target: MentionLite) => {
      closeAll()
      onFocusEntity?.(entity.id)
      const node = findMentionAnchor(entity.id, target.segment_id, isSpeakingMention(target))
      if (!node) return
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      node.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
    },
    [closeAll, entity.id, onFocusEntity],
  )

  // Index peek: leave for the transcript. Close first — the tab switch
  // may unmount the transcript the target anchor lives in.
  const showInTranscript = useCallback(
    (segmentId: number) => {
      closeAll()
      onShowInTranscript?.(segmentId)
    },
    [closeAll, onShowInTranscript],
  )

  const anchorId = isIndex
    ? undefined
    : isSpeaker
      ? speakerAnchorId(entity.id, mention.segment_id)
      : mentionPermalinkHash(entity.id, mention.segment_id)

  const look = isIndex
    ? (className ?? '')
    : isSpeaker
      ? 'text-inherit decoration-dotted underline-offset-2 hover:underline'
      : `underline ${style.inlineUnderline} hover:bg-gray-50`

  const ariaLabel = `${entity.canonical_name}, ${style.label}, ${
    episodeEntity.mention_count
  } mention${episodeEntity.mention_count === 1 ? '' : 's'}`

  const showCard = cardShowing && cardPosition && typeof document !== 'undefined'
  const showSheet = !isSmUp && pinned

  return (
    <>
      <a
        ref={linkRef}
        href={entityHref(entity.type, entity.id)}
        id={anchorId}
        data-variant={variant}
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
        className={`${look} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded-sm ${
          pinned ? 'bg-gray-100' : ''
        }`}
      >
        {children}
      </a>
      {showCard
        ? createPortal(
            <span
              ref={cardRef}
              // Portal-rendered so ancestor `overflow:hidden` can't clip
              // the card, and so we can flip horizontally near the
              // viewport's right edge. Transient rung (spec #71): the
              // card must paint over the reader overlay (`z-[45]`).
              onMouseEnter={open}
              onMouseLeave={scheduleClose}
              onClick={stopReactPropagation}
              onKeyDown={stopReactPropagation}
              style={{
                position: 'fixed',
                top: cardPosition.top,
                left: cardPosition.left,
                width: CARD_WIDTH_PX,
                zIndex: TRANSIENT_LAYER_Z,
              }}
            >
              <EntityHoverCard
                episodeEntity={episodeEntity}
                mention={mention}
                episodeId={episodeId}
                summaryEnabled={pinned || hoverSettled}
                onSeek={onSeek}
                onJumpToMention={jumpToMention}
                onShowInTranscript={onShowInTranscript ? showInTranscript : undefined}
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
            onShowInTranscript={onShowInTranscript ? showInTranscript : undefined}
            onNavigate={closeAll}
            sheet
          />
        </EntityPeekSheet>
      )}
    </>
  )
}
