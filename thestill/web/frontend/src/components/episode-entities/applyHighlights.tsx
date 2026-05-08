import { Fragment, type ReactElement, type ReactNode } from 'react'
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
// Search-highlight integration: when a `searchQuery` is provided, the
// function interleaves yellow `<mark>` spans for query matches both in
// the gaps between entity spans and inside each entity anchor's
// children. So a segment with an entity mention ("Stripe") and a
// separate search hit ("outcome") shows both decorations, and a search
// match that overlaps the entity surface form lands as a `<mark>`
// inside the entity `<a>`.

// Wrap every case-insensitive occurrence of `query` in `text` with a
// yellow `<mark>`. Returns the original string when the query is empty
// or has no matches, so callers can compose results without changing
// type-shape between the hit / no-hit cases.
export function highlightMatches(
  text: string,
  query: string,
): string | (string | ReactElement)[] {
  if (!query) return text
  const needle = query.toLowerCase()
  const hay = text.toLowerCase()
  const out: (string | ReactElement)[] = []
  let cursor = 0
  let idx = hay.indexOf(needle, cursor)
  if (idx === -1) return text
  let markKey = 0
  while (idx !== -1) {
    if (idx > cursor) out.push(text.slice(cursor, idx))
    out.push(
      <mark key={markKey++} className="bg-yellow-100 text-gray-900 rounded px-0.5">
        {text.slice(idx, idx + query.length)}
      </mark>,
    )
    cursor = idx + query.length
    idx = hay.indexOf(needle, cursor)
  }
  if (cursor < text.length) out.push(text.slice(cursor))
  return out
}

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
  // The current transcript-search query. When non-empty, yellow
  // `<mark>` spans are interleaved with the entity spans (both in
  // the gaps and inside entity anchors).
  searchQuery?: string
  onSeek?: (seconds: number) => void
  onFocusEntity?: (entityId: string) => void
}

// When entity highlighting is disabled or no spans land, fall back to
// the search-only render. Otherwise place entity anchors and run the
// search-mark pass on every text slice (gaps and entity children) so
// both decorations appear at once.
export function applyEntityHighlights({
  text,
  segmentMentions,
  enabled,
  searchQuery,
  onSeek,
  onFocusEntity,
}: ApplyEntityHighlightsOptions): ReactNode {
  const query = searchQuery ?? ''
  const searchOnly = (): ReactNode => highlightMatches(text, query) as ReactNode

  if (!enabled || !segmentMentions || segmentMentions.mentions.length === 0) {
    return searchOnly()
  }

  // Apply confidence floor (spec §5.2 visual rules: mentions below the
  // extractor confidence threshold render as plain text).
  const eligible = segmentMentions.mentions.filter(
    (m) => m.confidence >= INLINE_HIGHLIGHT_CONFIDENCE_FLOOR,
  )
  if (eligible.length === 0) {
    return searchOnly()
  }

  const spans = buildSpans(text, eligible)
  if (spans.length === 0) {
    return searchOnly()
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
      const gap = text.slice(cursor, span.start)
      nodes.push(
        <Fragment key={`pre-${span.start}`}>{highlightMatches(gap, query)}</Fragment>,
      )
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
          {highlightMatches(matched, query)}
        </EntityHighlight>,
      )
    } else {
      // Defensive — should never happen because we filter `eligible`
      // off the same map. If it does, fall back to plain text rather
      // than dropping the span entirely.
      nodes.push(
        <Fragment key={`m-fallback-${spanKey}`}>{highlightMatches(matched, query)}</Fragment>,
      )
    }
    cursor = span.end
  }
  if (cursor < text.length) {
    const tail = text.slice(cursor)
    nodes.push(<Fragment key="tail">{highlightMatches(tail, query)}</Fragment>)
  }
  return <>{nodes}</>
}
