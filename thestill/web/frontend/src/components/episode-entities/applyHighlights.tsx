import { Fragment, type ReactNode } from 'react'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import EntityHighlight from './EntityHighlight'
import { INLINE_HIGHLIGHT_CONFIDENCE_FLOOR } from '../../utils/entityColors'

// Spec #28 §5.2 — inline entity highlights inside transcript segments.
//
// We don't store character offsets per mention, only `surface_form`.
// So for each segment we walk its text and locate each mention's
// surface form by case-insensitive substring search, picking
// non-overlapping spans (longest surface form wins on conflict).
//
// This is run after the existing yellow search-highlight pass — entity
// highlights are inert wrt the search highlight: when an entity span
// overlaps a search match, both render (the entity wraps; the search
// `<mark>` sits inside the entity's `<a>` text content).

export interface SegmentMentionSet {
  // Entity records for the entities mentioned in this segment, keyed by
  // entity_id so we can look up styling + hover-card data per mention.
  entityById: Map<string, EpisodeEntity>
  // The mentions that fall inside this segment.
  mentions: MentionLite[]
}

interface Span {
  start: number
  end: number
  mention: MentionLite
}

// Build a list of non-overlapping spans by greedy left-to-right
// scanning. For each mention surface, find its first occurrence after
// the last placed span; if found, place it. Surface forms are sorted
// long-first so "Andrej Karpathy" wins over "Karpathy" for the first
// match position.
function buildSpans(text: string, mentions: MentionLite[]): Span[] {
  const ordered = mentions
    .slice()
    .sort((a, b) => b.surface_form.length - a.surface_form.length)
  const lower = text.toLowerCase()
  const spans: Span[] = []
  for (const m of ordered) {
    const needle = m.surface_form.toLowerCase()
    if (!needle) continue
    let idx = lower.indexOf(needle)
    while (idx !== -1) {
      const end = idx + needle.length
      const overlap = spans.some((s) => !(end <= s.start || idx >= s.end))
      if (!overlap) {
        spans.push({ start: idx, end, mention: m })
        break
      }
      idx = lower.indexOf(needle, idx + 1)
    }
  }
  return spans.sort((a, b) => a.start - b.start)
}

export interface ApplyEntityHighlightsOptions {
  text: string
  segmentMentions: SegmentMentionSet | null
  enabled: boolean
  // Already-rendered ReactNode array from the search-yellow highlight
  // pass. We re-walk the original text by char offsets to wrap entity
  // spans, then splice the existing nodes back in for the gaps. The
  // search nodes are passed in so callers can interleave both passes.
  existingNodes?: ReactNode | ReactNode[]
  onSeek?: (seconds: number) => void
  onFocusEntity?: (entityId: string) => void
}

// When entity highlighting is disabled or there are no mentions, fall
// back to the search-rendered nodes. Otherwise wrap each span with an
// `<EntityHighlight>` and return the recomposed node array.
//
// We deliberately discard the existing search-highlight nodes when
// entity highlights are placed: re-interleaving search-mark
// boundaries with entity-anchor boundaries is tricky and the spec
// flags affordance #2 ("E toggle") for users who want to read without
// underlines. The 99% case is that entity highlights are on and the
// search is empty.
export function applyEntityHighlights({
  text,
  segmentMentions,
  enabled,
  existingNodes,
  onSeek,
  onFocusEntity,
}: ApplyEntityHighlightsOptions): ReactNode {
  if (!enabled || !segmentMentions || segmentMentions.mentions.length === 0) {
    return existingNodes ?? text
  }

  // Apply confidence floor (spec §5.2 visual rules: mentions below the
  // extractor confidence threshold render as plain text).
  const eligible = segmentMentions.mentions.filter(
    (m) => m.confidence >= INLINE_HIGHLIGHT_CONFIDENCE_FLOOR,
  )
  if (eligible.length === 0) {
    return existingNodes ?? text
  }

  const spans = buildSpans(text, eligible)
  if (spans.length === 0) {
    return existingNodes ?? text
  }

  // Compose React keys from the span position rather than mention.id.
  // mention.id is the SQLite AUTOINCREMENT pk and is normally unique,
  // but the API serializer falls back to 0 when the value is missing,
  // and two such mentions in the same segment would collide. The span's
  // (start, end) is unique by construction (non-overlapping).
  const nodes: ReactNode[] = []
  let cursor = 0
  for (let i = 0; i < spans.length; i += 1) {
    const span = spans[i]
    if (span.start > cursor) {
      nodes.push(<Fragment key={`pre-${span.start}`}>{text.slice(cursor, span.start)}</Fragment>)
    }
    const entity = segmentMentions.entityById.get(span.mention.entity_id)
    const matched = text.slice(span.start, span.end)
    const spanKey = `m-${span.start}-${span.end}-${span.mention.entity_id}`
    if (entity) {
      nodes.push(
        <EntityHighlight
          key={spanKey}
          episodeEntity={entity}
          mention={span.mention}
          onSeek={onSeek}
          onFocusEntity={onFocusEntity}
        >
          {matched}
        </EntityHighlight>,
      )
    } else {
      // Defensive — should never happen because we filter `eligible`
      // off the same map. If it does, fall back to plain text rather
      // than dropping the span entirely.
      nodes.push(<Fragment key={`m-fallback-${spanKey}`}>{matched}</Fragment>)
    }
    cursor = span.end
  }
  if (cursor < text.length) {
    nodes.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>)
  }
  return <>{nodes}</>
}
