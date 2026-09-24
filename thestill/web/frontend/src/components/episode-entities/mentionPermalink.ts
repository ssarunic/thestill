import type { MentionLite } from '../../api/types'

// Spec #28 §5.2 affordance #14 — `#m=<entity_id>:<segment_id>` hash
// permalinks. Every inline highlight carries this as its DOM id so MCP
// tools / shared links can deep-anchor a specific mention, and the peek's
// prev/next can scroll to a sibling mention's anchor.
export function mentionPermalinkHash(entityId: string, segmentId: number): string {
  return `m=${entityId}:${segmentId}`
}

// The speaker label of a segment is a second anchor for the same
// (entity, segment): the person is *speaking* there rather than being
// named in the text. Its own id keeps the two from colliding when a host
// says their own name.
export function speakerAnchorId(entityId: string, segmentId: number): string {
  return `${mentionPermalinkHash(entityId, segmentId)}:speaker`
}

// A `speaking` mention is the extractor's record of who said a segment
// (one per segment per speaker); every other role is a name in the text.
export function isSpeakingMention(mention: Pick<MentionLite, 'role'>): boolean {
  return mention.role === 'speaking'
}

export function findMentionAnchor(entityId: string, segmentId: number, speaking: boolean): HTMLElement | null {
  if (typeof document === 'undefined') return null
  return document.getElementById(
    speaking ? speakerAnchorId(entityId, segmentId) : mentionPermalinkHash(entityId, segmentId),
  )
}
