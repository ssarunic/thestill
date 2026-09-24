// Spec #28 §5.2 affordance #14 — `#m=<entity_id>:<segment_id>` hash
// permalinks. Every inline highlight carries this as its DOM id so MCP
// tools / shared links can deep-anchor a specific mention, and the peek's
// prev/next can scroll to a sibling mention's anchor.
export function mentionPermalinkHash(entityId: string, segmentId: number): string {
  return `m=${entityId}:${segmentId}`
}
